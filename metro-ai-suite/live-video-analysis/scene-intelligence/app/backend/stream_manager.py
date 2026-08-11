# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""PyAV-based RTSP relay into MediaMTX for WebRTC rendering.

Each :class:`StreamManager` owns one video source. A dedicated daemon thread
opens the source with PyAV and remuxes its packets (stream-copy, no re-encode)
into MediaMTX over RTSP. MediaMTX then serves the stream to the browser over
WebRTC (WHEP), giving low-latency playback without decoding on the backend.

Because this path is a pure remux, it leaves the compressed frames untouched;
a second connection (see ``_segment_and_register_loop``) decodes the same
source for segment writing, frame registration, and VLM inference handoff.
"""

from __future__ import annotations

import errno
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import av

from backend.config import settings
from backend.frame_registry import FrameRecord, SegmentFrameRegistry

logger = logging.getLogger(__name__)

# Area-averaging gives better downscale quality than the default bilinear;
# fall back for older PyAV/ffmpeg builds that don't expose it.
_REFORMAT_INTERPOLATION = getattr(
    av.video.reformatter.Interpolation, "AREA", av.video.reformatter.Interpolation.BILINEAR
)


@dataclass
class StreamHealth:
    """Runtime snapshot for one stream."""

    publishing: bool = False
    resolution: Optional[str] = None  # e.g. "1920x1080"
    codec: Optional[str] = None
    reconnect_count: int = 0
    last_packet_ts: Optional[float] = None  # monotonic
    caption: Optional[str] = None
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
        frame_registry: Optional[SegmentFrameRegistry] = None,
    ):
        self.stream_id = stream_id
        self.source_url = source_url
        self.vlm_prompt = str(vlm_prompt or "").strip()
        self.alert_event = " ".join(str(alert_event or "").strip().split())
        self.target_url = f"{settings.WEBRTC_RELAY_URL.rstrip('/')}/{stream_id}"

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._infer_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.health = StreamHealth()

        # Latest sampled frame handed from the segment loop to the inference worker.
        self._frame_lock = threading.Lock()
        self._latest_frame = None  # np.ndarray (H×W×3, RGB) or None
        self._latest_frame_ts: float = 0.0  # monotonic; 0 = no frame yet
        self._latest_frame_id: Optional[uuid.UUID] = None  # ties back to the registered FrameRecord

        # Segment writer + frame metadata registry (deep-analysis handoff).
        self.frame_registry = frame_registry
        self._segment_thread: Optional[threading.Thread] = None
        self._segment_output_pattern = str(
            Path(settings.SEGMENT_OUTPUT_DIR) / f"{stream_id}_segment_%04d.mp4"
        )
        self._finalized_segments: list[str] = []  # oldest-first; reclaimed past SEGMENT_MAX_ON_DISK

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

        if self.frame_registry is not None:
            Path(settings.SEGMENT_OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
            self._segment_thread = threading.Thread(
                target=self._segment_and_register_loop,
                daemon=True,
                name=f"segment-{self.stream_id}",
            )
            self._segment_thread.start()
            logger.info("Started segment writer for '%s'", self.stream_id)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
        if self._infer_thread:
            self._infer_thread.join(timeout=5.0)
        if self._segment_thread:
            self._segment_thread.join(timeout=5.0)
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
        inference_count = 0
        while self._running:
            with self._frame_lock:
                frame = self._latest_frame
                frame_ts = self._latest_frame_ts
                frame_id = self._latest_frame_id

            if frame is None or frame_ts == processed_ts:
                time.sleep(0.2)  # nothing new to caption yet
                continue
            processed_ts = frame_ts
            frame = self._maybe_resize_frame_for_vlm(frame)
            logger.info("[%s] VLM inferencing on frame_id=%s", self.stream_id, frame_id)

            try:
                caption, metrics = engine.caption_with_metrics(
                    frame, prompt=self.vlm_prompt, priority=bool(self.alert_event)
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[%s] VLM inference error: %s", self.stream_id, exc)
                continue

            logger.info("[%s] VLM inference done for frame_id=%s: %s", self.stream_id, frame_id, caption)

            with self._lock:
                self.health.caption = caption
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

            inference_count += 1
            if settings.VLM_MAX_INFERENCES and inference_count >= settings.VLM_MAX_INFERENCES:
                logger.info(
                    "[%s] VLM_MAX_INFERENCES=%d reached; inference worker stopping (relay/segments unaffected)",
                    self.stream_id,
                    settings.VLM_MAX_INFERENCES,
                )
                break

        logger.info("Inference worker exited for stream '%s'", self.stream_id)

    def _maybe_resize_frame_for_vlm(self, frame):
        """Resize a sampled RGB frame to ``VLM_FRAME_RESIZE`` before captioning, if configured."""
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
                .reformat(width=target_w, height=target_h, format="rgb24", interpolation=_REFORMAT_INTERPOLATION)
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

    def _segment_and_register_loop(self) -> None:
        """Sample the source once per stream: register + segment, then hand off to VLM.

        Runs on its own connection, independent of the relay, so a slow or
        stalled writer never affects playback. Per decoded frame the flow is:
        get the frame from RTSP -> register its metadata and mux it into the
        rolling segment -> at the (lower) ``VLM_INTERVAL`` cadence, hand that
        same frame to the inference worker via ``self._latest_frame``. Only
        frame metadata (frame_id, segment_path, pts) is kept in memory via
        ``self.frame_registry`` — no frame images are persisted to disk; a deep
        analyzer resolves ``frame_id`` back to ``segment_path`` on demand.
        """
        assert self.frame_registry is not None

        vlm_active = settings.VLM_ENABLED and bool(self.vlm_prompt)
        last_vlm_sample = 0.0

        segments_written = 0
        last_segment_idx = -1
        last_segment_path: Optional[str] = None
        segment_limit_reached = False

        backoff = 2.0
        max_backoff = 20.0

        while self._running and not segment_limit_reached:
            input_container = None
            output_container = None
            try:
                input_container = av.open(
                    self.source_url,
                    options=self._input_options(),
                    timeout=settings.RTSP_TIMEOUT,
                )
                in_stream = input_container.streams.video[0]
                in_stream.thread_type = "AUTO"

                new_width, new_height = _calculate_scaled_dimensions(
                    in_stream.width, in_stream.height
                )
                logger.info(
                    "[%s] source=%dx%d (aspect=%.4f) -> encode=%dx%d",
                    self.stream_id,
                    in_stream.width,
                    in_stream.height,
                    in_stream.width / in_stream.height,
                    new_width,
                    new_height,
                )

                output_container = av.open(
                    self._segment_output_pattern,
                    mode="w",
                    format="stream_segment",
                    options={"segment_time": str(settings.SEGMENT_TIME_SECONDS)},
                )
                out_stream = output_container.add_stream(
                    "libx264", rate=in_stream.average_rate
                )
                out_stream.width = new_width
                out_stream.height = new_height
                out_stream.pix_fmt = "yuv420p"
                out_stream.thread_type = "AUTO"

                reformatter = av.video.reformatter.VideoReformatter()

                avg_fps = float(in_stream.average_rate)
                sample_every = max(1, int(avg_fps / settings.FRAME_SAMPLE_FPS))
                backoff = 2.0
                frame_count = 0

                for packet in input_container.demux(in_stream):
                    if not self._running or segment_limit_reached:
                        break

                    for frame in packet.decode():
                        frame_count += 1

                        if frame.width != new_width or frame.height != new_height:
                            frame = reformatter.reformat(
                                frame, width=new_width, height=new_height, interpolation=_REFORMAT_INTERPOLATION
                            )

                        pts_seconds = (
                            float(frame.pts * in_stream.time_base)
                            if frame.pts is not None
                            else frame_count / avg_fps
                        )
                        segment_idx = int(pts_seconds / settings.SEGMENT_TIME_SECONDS)
                        segment_path = self._segment_output_pattern.replace(
                            "%04d", f"{segment_idx:04d}"
                        )

                        if segment_idx != last_segment_idx:
                            if last_segment_path is not None:
                                self._reclaim_old_segments(last_segment_path)
                            last_segment_idx = segment_idx
                            last_segment_path = segment_path
                            segments_written += 1
                            if settings.MAX_SEGMENTS and segments_written > settings.MAX_SEGMENTS:
                                segment_limit_reached = True
                                logger.info(
                                    "[%s] MAX_SEGMENTS=%d reached; segment writer stopping (relay/VLM unaffected)",
                                    self.stream_id,
                                    settings.MAX_SEGMENTS,
                                )
                                break

                        if frame_count % sample_every == 0:
                            frame_id = uuid.uuid4()
                            self.frame_registry.register(
                                FrameRecord(
                                    frame_id=frame_id,
                                    stream_id=self.stream_id,
                                    rtsp_url=self.source_url,
                                    segment_path=segment_path,
                                    pts_seconds=pts_seconds,
                                )
                            )
                            logger.info(
                                "[%s] registered frame_id=%s segment=%s pts=%.2fs",
                                self.stream_id,
                                frame_id,
                                segment_path,
                                pts_seconds,
                            )

                            if vlm_active:
                                now = time.monotonic()
                                if now - last_vlm_sample >= settings.VLM_INTERVAL:
                                    last_vlm_sample = now
                                    rgb = frame.to_ndarray(format="rgb24")
                                    with self._frame_lock:
                                        self._latest_frame = rgb
                                        self._latest_frame_ts = now
                                        self._latest_frame_id = frame_id
                                    logger.info(
                                        "[%s] handed frame_id=%s to VLM inference worker",
                                        self.stream_id,
                                        frame_id,
                                    )

                        for enc_packet in out_stream.encode(frame):
                            output_container.mux(enc_packet)

                for enc_packet in out_stream.encode(None):
                    output_container.mux(enc_packet)

            except av.error.FFmpegError as exc:
                logger.warning("[%s] segment writer error: %s", self.stream_id, exc)
            except Exception as exc:  # noqa: BLE001 - keep the loop resilient
                logger.warning("[%s] segment writer loop error: %s", self.stream_id, exc)
            finally:
                for container in (output_container, input_container):
                    if container is not None:
                        try:
                            container.close()
                        except Exception:  # noqa: BLE001
                            pass

            if not self._running or segment_limit_reached:
                break
            time.sleep(backoff)
            backoff = min(backoff * 1.5, max_backoff)

        logger.info("Segment writer loop exited for stream '%s'", self.stream_id)

    def _reclaim_old_segments(self, finalized_segment_path: str) -> None:
        """Delete this stream's oldest finalized segments once over SEGMENT_MAX_ON_DISK.

        Only ever called with a segment that has already rotated out (never
        the one ``ffmpeg`` is still writing), so this can't delete an open file.
        """
        if not settings.SEGMENT_MAX_ON_DISK:
            return
        self._finalized_segments.append(finalized_segment_path)
        while len(self._finalized_segments) > settings.SEGMENT_MAX_ON_DISK:
            self._delete_segment(self._finalized_segments.pop(0))

    def _delete_segment(self, segment_path: str) -> None:
        try:
            Path(segment_path).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("[%s] failed to delete segment %s: %s", self.stream_id, segment_path, exc)
            return
        removed = self.frame_registry.remove_segment(self.stream_id, segment_path) if self.frame_registry else 0
        logger.info(
            "[%s] reclaimed segment=%s (removed %d registry entries)",
            self.stream_id,
            segment_path,
            removed,
        )


def _calculate_scaled_dimensions(width: int, height: int) -> tuple[int, int]:
    """Snap to the closest benchmarked segment-recording dimensions for the source aspect ratio.

    Picks the nearest of SEGMENT_DIM_1_1/4_3/16_9 by aspect ratio, oriented to
    match the source (landscape vs portrait). All presets are even, as
    required by libx264.
    """
    is_portrait = height > width
    long_side, short_side = (height, width) if is_portrait else (width, height)
    ratio = long_side / short_side

    presets = (
        (1.0, settings.SEGMENT_DIM_1_1),
        (4 / 3, settings.SEGMENT_DIM_4_3),
        (16 / 9, settings.SEGMENT_DIM_16_9),
    )
    _, (preset_w, preset_h) = min(presets, key=lambda item: abs(item[0] - ratio))

    new_width, new_height = (preset_h, preset_w) if is_portrait else (preset_w, preset_h)
    new_width = new_width if new_width % 2 == 0 else new_width - 1
    new_height = new_height if new_height % 2 == 0 else new_height - 1
    return new_width, new_height
