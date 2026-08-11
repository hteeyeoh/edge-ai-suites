# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""PyAV-based RTSP relay into MediaMTX for WebRTC rendering.

Each :class:`StreamManager` owns one video source. A dedicated daemon thread
opens the source with PyAV and remuxes its packets (stream-copy, no re-encode)
into MediaMTX over RTSP. MediaMTX then serves the stream to the browser over
WebRTC (WHEP), giving low-latency playback without decoding on the backend.

Because this path is a pure remux, it leaves the compressed frames untouched;
a later step can add a PyAV decode branch off the same source for VLM
inference.
"""

from __future__ import annotations

import errno
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import av

from backend.config import settings

logger = logging.getLogger(__name__)


@dataclass
class StreamHealth:
    """Runtime snapshot for one stream."""

    publishing: bool = False
    resolution: Optional[str] = None  # e.g. "1920x1080"
    codec: Optional[str] = None
    reconnect_count: int = 0
    last_packet_ts: Optional[float] = None  # monotonic
    caption: Optional[str] = None
    alerts: list[dict[str, Any]] = field(default_factory=list)
    caption_ts: Optional[float] = None  # wall clock (epoch seconds)
    ttft_ms: Optional[float] = None
    tpot_ms: Optional[float] = None
    throughput_tps: Optional[float] = None


class StreamManager:
    """Relay a single video source into MediaMTX with PyAV."""

    def __init__(
        self,
        stream_id: str,
        source_url: str,
        vlm_prompt: str = "",
        alert_event: str = "",
    ):
        self.stream_id = stream_id
        self.source_url = source_url
        self.vlm_prompt = str(vlm_prompt or "").strip()
        self.alert_event = " ".join(str(alert_event or "").strip().split())
        self.target_url = f"{settings.WEBRTC_RELAY_URL.rstrip('/')}/{stream_id}"

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._sample_thread: Optional[threading.Thread] = None
        self._infer_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.health = StreamHealth()

        # Latest sampled frame handed from the sampler to the inference worker.
        self._frame_lock = threading.Lock()
        self._latest_frame = None  # np.ndarray (H×W×3, RGB) or None
        self._latest_frame_ts: float = 0.0  # monotonic; 0 = no frame yet

        # Alert state machine: debounce repeated detections and only raise
        # transitions (OFF->ON / ON->OFF), not every inference tick.
        self._alert_is_on = False
        self._positive_streak = 0
        self._negative_streak = 0
        self._last_raise_ts = float("-inf")

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._relay_loop,
            daemon=True,
            name=f"relay-{self.stream_id}",
        )
        self._thread.start()
        logger.info(
            "Started relay '%s' (%s -> %s)",
            self.stream_id,
            self.source_url,
            self.target_url,
        )

        if settings.VLM_ENABLED and self.vlm_prompt:
            self._sample_thread = threading.Thread(
                target=self._sample_loop,
                daemon=True,
                name=f"sample-{self.stream_id}",
            )
            self._sample_thread.start()
            self._infer_thread = threading.Thread(
                target=self._infer_worker,
                daemon=True,
                name=f"infer-{self.stream_id}",
            )
            self._infer_thread.start()
            logger.info("Started VLM inference for '%s'", self.stream_id)
        elif settings.VLM_ENABLED:
            logger.info(
                "VLM prompt not set for '%s' — captions disabled until stream is re-added with a prompt",
                self.stream_id,
            )

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
        if self._sample_thread:
            self._sample_thread.join(timeout=5.0)
        if self._infer_thread:
            self._infer_thread.join(timeout=5.0)
        with self._lock:
            self.health.publishing = False
        logger.info("Stopped relay '%s'", self.stream_id)

    # ------------------------------------------------------------------ #
    # Consumers
    # ------------------------------------------------------------------ #

    def get_health(self) -> StreamHealth:
        with self._lock:
            return StreamHealth(
                publishing=self.health.publishing,
                resolution=self.health.resolution,
                codec=self.health.codec,
                reconnect_count=self.health.reconnect_count,
                last_packet_ts=self.health.last_packet_ts,
                caption=self.health.caption,
                alerts=[dict(item) for item in self.health.alerts],
                caption_ts=self.health.caption_ts,
                ttft_ms=self.health.ttft_ms,
                tpot_ms=self.health.tpot_ms,
                throughput_tps=self.health.throughput_tps,
            )

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _input_options(self) -> dict:
        if str(self.source_url).startswith(("rtsp://", "rtsps://")):
            return {
                "rtsp_transport": "tcp",
                "fflags": "nobuffer",
                "flags": "low_delay",
            }
        return {}

    def _relay_loop(self) -> None:
        if not settings.WEBRTC_AUTO_PUBLISH:
            logger.info("WEBRTC_AUTO_PUBLISH disabled — relay '%s' idle", self.stream_id)
            return

        is_rtsp = str(self.source_url).startswith(("rtsp://", "rtsps://"))
        backoff = 1.0
        max_backoff = 20.0

        while self._running:
            input_container = None
            output_container = None
            try:
                input_container = av.open(
                    self.source_url,
                    options=self._input_options(),
                    timeout=settings.RTSP_TIMEOUT,
                )
                in_stream = input_container.streams.video[0]
                in_stream.thread_type = "NONE"  # remux only, never decode

                output_container = av.open(
                    self.target_url,
                    mode="w",
                    format="rtsp",
                    options={"rtsp_transport": "tcp"},
                )
                out_stream = output_container.add_stream_from_template(in_stream)

                with self._lock:
                    self.health.publishing = True
                    self.health.codec = in_stream.codec_context.name
                    self.health.resolution = (
                        f"{in_stream.codec_context.width}x{in_stream.codec_context.height}"
                    )
                backoff = 1.0
                logger.info("Relay '%s' publishing to MediaMTX", self.stream_id)

                last_dts = None
                mux_errors = 0
                backward = 0
                for packet in input_container.demux(in_stream):
                    if not self._running:
                        break
                    if packet.dts is None:
                        continue  # skip non-timed packets (e.g. header flushes)

                    # Cameras occasionally emit backward/duplicate timestamps.
                    # The RTSP muxer rejects those with EINVAL, so drop them
                    # here instead of tearing the whole session down. If the
                    # clock genuinely reset (many backward in a row), adopt the
                    # new timeline rather than dropping frames forever.
                    if last_dts is not None and packet.dts <= last_dts:
                        backward += 1
                        if backward <= 10:
                            continue
                    backward = 0
                    last_dts = packet.dts

                    packet.stream = out_stream
                    try:
                        output_container.mux(packet)
                        mux_errors = 0
                    except av.error.FFmpegError as exc:
                        if exc.errno == errno.EINVAL:
                            mux_errors += 1
                            if mux_errors <= 30:
                                continue  # skip the bad packet, keep publishing
                        raise  # persistent/other error → reconnect

                    with self._lock:
                        self.health.last_packet_ts = time.monotonic()

                    # Local files have no wall-clock pacing; play at real time.
                    if not is_rtsp and packet.duration:
                        time.sleep(float(packet.duration * in_stream.time_base))

            except av.error.FFmpegError as exc:
                logger.warning("[%s] PyAV relay error: %s", self.stream_id, exc)
            except Exception as exc:  # noqa: BLE001 - keep the loop resilient
                logger.warning("[%s] relay error: %s", self.stream_id, exc)
            finally:
                for container in (output_container, input_container):
                    if container is not None:
                        try:
                            container.close()
                        except Exception:  # noqa: BLE001
                            pass

            if not self._running:
                break

            with self._lock:
                self.health.publishing = False
                self.health.reconnect_count += 1
            logger.info("[%s] reconnecting relay in %.0fs ...", self.stream_id, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 1.5, max_backoff)

        logger.info("Relay loop exited for stream '%s'", self.stream_id)

    def _sample_loop(self) -> None:
        """Continuously drain the source and sample one keyframe per interval.

        Runs on its own connection so the low-latency remux path stays a pure
        stream-copy. The socket is drained on every iteration (cheap demux, no
        decode) so it never backs up; only one self-contained keyframe is
        decoded per ``VLM_INTERVAL`` and handed to the inference worker. This
        keeps CPU load minimal so the relay thread is never starved.
        """
        backoff = 2.0
        max_backoff = 30.0

        while self._running:
            container = None
            try:
                container = av.open(
                    self.source_url,
                    options=self._input_options(),
                    timeout=settings.RTSP_TIMEOUT,
                )
                v_stream = container.streams.video[0]
                v_stream.thread_type = "NONE"  # decode lone keyframes on demand
                backoff = 2.0
                last_sample = 0.0

                for packet in container.demux(v_stream):
                    if not self._running:
                        break
                    if packet.dts is None:
                        continue

                    now = time.monotonic()
                    if now - last_sample < settings.VLM_INTERVAL:
                        continue  # drain cheaply; do not decode
                    if not packet.is_keyframe:
                        continue  # wait for a keyframe (self-contained to decode)

                    try:
                        frames = packet.decode()
                    except av.error.FFmpegError:
                        continue
                    if not frames:
                        continue

                    last_sample = now
                    rgb = frames[-1].to_ndarray(format="rgb24")
                    with self._frame_lock:
                        self._latest_frame = rgb
                        self._latest_frame_ts = now

            except av.error.FFmpegError as exc:
                logger.warning("[%s] VLM sampler error: %s", self.stream_id, exc)
            except Exception as exc:  # noqa: BLE001 - keep the loop resilient
                logger.warning("[%s] VLM sampler loop error: %s", self.stream_id, exc)
            finally:
                if container is not None:
                    try:
                        container.close()
                    except Exception:  # noqa: BLE001
                        pass

            if not self._running:
                break
            time.sleep(backoff)
            backoff = min(backoff * 1.5, max_backoff)

        logger.info("Sampler loop exited for stream '%s'", self.stream_id)

    def _infer_worker(self) -> None:
        """Caption the most recent sampled frame, off the packet-draining path.

        The VLM ``generate`` call blocks for seconds on CPU; running it here
        instead of inside the sampler keeps the source socket drained the whole
        time, so inference bursts never stall the relay.
        """
        from backend.vlm import get_vlm_engine

        try:
            engine = get_vlm_engine()
        except Exception as exc:  # noqa: BLE001 - model may be missing/misconfigured
            logger.error("[%s] VLM disabled: %s", self.stream_id, exc)
            return

        processed_ts = 0.0
        while self._running:
            with self._frame_lock:
                frame = self._latest_frame
                frame_ts = self._latest_frame_ts

            if frame is None or frame_ts == processed_ts:
                time.sleep(0.2)  # nothing new to caption yet
                continue
            processed_ts = frame_ts
            frame = self._maybe_resize_frame_for_vlm(frame)

            try:
                alert_payload, metrics = engine.infer_alert_with_metrics(
                    frame,
                    prompt=self.vlm_prompt,
                    alert_event=self.alert_event,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[%s] VLM inference error: %s", self.stream_id, exc)
                continue

            alerts = alert_payload.get("alerts") if isinstance(alert_payload, dict) else []
            first_alert = alerts[0] if isinstance(alerts, list) and alerts else {}
            event_name = str(first_alert.get("event") or self.alert_event).strip() or self.alert_event
            model_triggered = bool(first_alert.get("triggered"))
            state_on, state_changed, raised_now = self._advance_alert_state(
                model_triggered,
                time.monotonic(),
            )
            caption = f"{event_name}: {'Yes' if state_on else 'No'}"
            logger.info("[%s] structured_alert_response=%s", self.stream_id, alert_payload)
            if raised_now:
                logger.info("[%s] alert transition OFF->ON for event '%s'", self.stream_id, event_name)
            elif state_changed:
                logger.info("[%s] alert transition ON->OFF for event '%s'", self.stream_id, event_name)

            with self._lock:
                self.health.caption = caption
                self.health.alerts = [{"event": event_name, "triggered": state_on}]
                if state_changed:
                    # Transition timestamp: stable signal for external consumers.
                    self.health.caption_ts = time.time()
                ttft_ms = metrics.get("ttft_ms")
                tpot_ms = metrics.get("tpot_ms")
                throughput_tps = metrics.get("throughput_tps")
                if ttft_ms is not None:
                    self.health.ttft_ms = ttft_ms
                if tpot_ms is not None:
                    self.health.tpot_ms = tpot_ms
                if throughput_tps is not None:
                    self.health.throughput_tps = throughput_tps
            logger.debug("[%s] caption: %s", self.stream_id, caption)

        logger.info("Inference worker exited for stream '%s'", self.stream_id)

    def _advance_alert_state(self, model_triggered: bool, now_monotonic: float) -> tuple[bool, bool, bool]:
        """Advance alert ON/OFF state with streaks and optional rearm cooldown.

        Returns ``(state_on, state_changed, raised_now)``.
        """
        if model_triggered:
            self._positive_streak += 1
            self._negative_streak = 0
        else:
            self._negative_streak += 1
            self._positive_streak = 0

        state_changed = False
        raised_now = False

        if not self._alert_is_on:
            if self._positive_streak >= settings.VLM_ALERT_ON_STREAK:
                self._alert_is_on = True
                state_changed = True
                if now_monotonic - self._last_raise_ts >= settings.VLM_ALERT_REARM_SEC:
                    raised_now = True
                    self._last_raise_ts = now_monotonic
        elif self._negative_streak >= settings.VLM_ALERT_OFF_STREAK:
            self._alert_is_on = False
            state_changed = True

        return self._alert_is_on, state_changed, raised_now

    def _maybe_resize_frame_for_vlm(self, frame):
        target = settings.VLM_FRAME_RESIZE
        if target is None:
            return frame

        target_w, target_h = target
        src_h = getattr(frame, "shape", (0, 0))[0]
        src_w = getattr(frame, "shape", (0, 0))[1]
        if src_w == target_w and src_h == target_h:
            return frame

        try:
            resized = (
                av.VideoFrame.from_ndarray(frame, format="rgb24")
                .reformat(width=target_w, height=target_h, format="rgb24")
                .to_ndarray(format="rgb24")
            )
            return resized
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[%s] VLM frame resize %sx%s -> %sx%s failed; using original frame (%s)",
                self.stream_id,
                src_w,
                src_h,
                target_w,
                target_h,
                exc,
            )
            return frame
