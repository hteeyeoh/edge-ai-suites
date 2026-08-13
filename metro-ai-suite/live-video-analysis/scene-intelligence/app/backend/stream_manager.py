# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""PyAV-based RTSP relay into MediaMTX for WebRTC rendering.

Each :class:`StreamManager` owns one video source. A single dedicated daemon
thread (``_stream_loop``) opens the source with PyAV *once* and, per demuxed
packet, does up to two jobs on that one connection:

* remux the packet (stream-copy, no re-encode) into MediaMTX over RTSP, which
  then serves the stream to the browser over WebRTC (WHEP) for low-latency
  playback; and
* decode it for segment writing, frame registration, and VLM inference
  handoff, folded into the same pass.

Both jobs share one input connection and one reconnect/backoff timeline
instead of opening the source twice, halving the load placed on the camera
and avoiding two independent, potentially out-of-sync reconnect cycles. When
segment writing/VLM is disabled (no ``frame_registry``), decoding is skipped
entirely and this degrades to the original pure remux path with no decode
CPU cost.
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
from backend.deep_analyzer import get_deep_analyzer
from backend.frame_registry import FrameRecord
from backend.frame_registry import SegmentFrameRegistry

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
        self._segment_output_pattern = str(
            Path(settings.SEGMENT_OUTPUT_DIR) / f"{stream_id}_segment_%04d.mp4"
        )
        self._finalized_segments: list[str] = []  # oldest-first; reclaimed past MAX_SEGMENTS

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        if self._running:
            return
        self._running = True

        if self.frame_registry is not None:
            Path(settings.SEGMENT_OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

        self._thread = threading.Thread(
            target=self._stream_loop,
            daemon=True,
            name=f"stream-{self.stream_id}",
        )
        self._thread.start()
        logger.info(
            "Started stream loop '%s' (%s -> %s, segments=%s)",
            self.stream_id,
            self.source_url,
            self.target_url,
            self.frame_registry is not None,
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

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
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

    def _stream_loop(self) -> None:
        """Single connection driving both the MediaMTX relay and segment/VLM sampling.

        Opens the source exactly once per (re)connect attempt and, per demuxed
        packet: remuxes it to MediaMTX (stream-copy) if relaying is enabled,
        and decodes it for segment writing / frame registration / VLM handoff
        if a ``frame_registry`` was supplied. Both jobs share one reconnect and
        backoff timeline. If only relaying is enabled, no decoding happens at
        all — this degrades to the original pure-remux, zero-decode-CPU path.

        Trade-off of sharing one connection: relay-side failures (a persistent
        mux error, or the input connection itself dropping) do reconnect
        segment writing too, since both read from the same demux loop.
        Segment-writer-side failures (e.g. a bad segments directory/disk
        error) are isolated and do *not* tear down a healthy relay — they
        just disable segment writing until the next reconnect attempt.

        Segment writing never stops on its own: MAX_SEGMENTS caps disk usage
        as a rolling buffer (oldest finalized segment deleted once the count
        is exceeded, see ``_reclaim_old_segments``), not a hard limit on
        writer lifetime. VLM_MAX_INFERENCES remains an independent cap on the
        VLM worker and, like MAX_SEGMENTS, never forces a relay reconnect.
        """
        do_relay = settings.WEBRTC_AUTO_PUBLISH
        do_segment = self.frame_registry is not None
        if not do_relay and not do_segment:
            logger.info(
                "WEBRTC_AUTO_PUBLISH disabled and no frame registry configured — stream '%s' idle",
                self.stream_id,
            )
            return

        is_rtsp = str(self.source_url).startswith(("rtsp://", "rtsps://"))
        vlm_active = do_segment and settings.VLM_ENABLED and bool(self.vlm_prompt)
        last_vlm_sample = 0.0

        last_segment_idx = -1
        last_segment_path: Optional[str] = None

        backoff = 1.0
        max_backoff = 20.0

        while self._running:
            input_container = None
            relay_output = None
            segment_output = None
            try:
                input_container = av.open(
                    self.source_url,
                    options=self._input_options(),
                    timeout=settings.RTSP_TIMEOUT,
                )
                in_stream = input_container.streams.video[0]
                in_stream.thread_type = "AUTO"

                relay_out_stream = None
                if do_relay:
                    relay_output = av.open(
                        self.target_url,
                        mode="w",
                        format="rtsp",
                        options={"rtsp_transport": "tcp"},
                    )
                    relay_out_stream = relay_output.add_stream_from_template(in_stream)

                    with self._lock:
                        self.health.publishing = True
                        self.health.codec = in_stream.codec_context.name
                        self.health.resolution = (
                            f"{in_stream.codec_context.width}x{in_stream.codec_context.height}"
                        )
                    logger.info("Relay '%s' publishing to MediaMTX", self.stream_id)

                segment_out_stream = None
                reformatter = None
                new_width = new_height = None
                avg_fps = None
                sample_every = None
                frame_count = 0
                if do_segment:
                    try:
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
                        segment_output = av.open(
                            self._segment_output_pattern,
                            mode="w",
                            format="stream_segment",
                            options={"segment_time": str(settings.SEGMENT_TIME_SECONDS)},
                        )
                        segment_out_stream = segment_output.add_stream(
                            "libx264", rate=in_stream.average_rate
                        )
                        segment_out_stream.width = new_width
                        segment_out_stream.height = new_height
                        segment_out_stream.pix_fmt = "yuv420p"
                        segment_out_stream.thread_type = "AUTO"
                        reformatter = av.video.reformatter.VideoReformatter()
                        avg_fps = float(in_stream.average_rate)
                        sample_every = max(1, int(avg_fps / settings.FRAME_SAMPLE_FPS))
                    except Exception as exc:  # noqa: BLE001 - e.g. permission/disk errors on the segments dir
                        # Segment writing is independent of the relay: don't let
                        # a bad segments directory (or any other setup failure)
                        # tear down an otherwise-healthy relay connection. Retry
                        # opening it on the next reconnect instead.
                        logger.warning(
                            "[%s] segment writer setup failed, continuing without segments this "
                            "connection (will retry next reconnect): %s",
                            self.stream_id,
                            exc,
                        )
                        if segment_output is not None:
                            try:
                                segment_output.close()
                            except Exception:  # noqa: BLE001
                                pass
                            segment_output = None
                        segment_out_stream = None

                backoff = 1.0
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
                    # new timeline rather than dropping frames forever. Applying
                    # this once, before decode/mux, keeps the relay output and
                    # the decoded segment/VLM frames on the same deduped timeline.
                    if last_dts is not None and packet.dts <= last_dts:
                        backward += 1
                        if backward <= 10:
                            continue
                    backward = 0
                    last_dts = packet.dts

                    # Decode before mutating packet.stream below (muxing rebinds
                    # the packet to the output stream/time_base); decode() itself
                    # doesn't touch that binding, so it's safe to do first.
                    decoded_frames = packet.decode() if segment_out_stream is not None else ()

                    if do_relay:
                        packet.stream = relay_out_stream
                        try:
                            relay_output.mux(packet)
                            mux_errors = 0
                        except av.error.FFmpegError as exc:
                            if exc.errno == errno.EINVAL and mux_errors < 30:
                                mux_errors += 1  # skip the bad packet, keep publishing
                            else:
                                raise  # persistent/other error → reconnect
                        else:
                            with self._lock:
                                self.health.last_packet_ts = time.monotonic()
                            # Local files have no wall-clock pacing; play at real time.
                            if not is_rtsp and packet.duration:
                                time.sleep(float(packet.duration * in_stream.time_base))

                    for frame in decoded_frames:
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
                                self._notify_segment_finalized(last_segment_path)
                            last_segment_idx = segment_idx
                            last_segment_path = segment_path

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
                                    with self._frame_lock:
                                        self._latest_frame = frame.to_ndarray(format="bgr24")
                                        self._latest_frame_ts = now
                                        self._latest_frame_id = frame_id
                                    logger.info(
                                        "[%s] handed frame_id=%s to VLM inference worker",
                                        self.stream_id,
                                        frame_id,
                                    )

                        try:
                            for enc_packet in segment_out_stream.encode(frame):
                                segment_output.mux(enc_packet)
                        except Exception as exc:  # noqa: BLE001 - keep relay alive on segment write failure
                            logger.warning(
                                "[%s] segment encode/mux failed, disabling segment writer for "
                                "this connection (will retry next reconnect): %s",
                                self.stream_id,
                                exc,
                            )
                            try:
                                segment_output.close()
                            except Exception:  # noqa: BLE001
                                pass
                            segment_output = None
                            segment_out_stream = None
                            break  # stop processing remaining decoded frames from this packet

                if segment_out_stream is not None:
                    try:
                        for enc_packet in segment_out_stream.encode(None):
                            segment_output.mux(enc_packet)
                    except Exception as exc:  # noqa: BLE001 - best-effort flush on clean reconnect
                        logger.warning(
                            "[%s] segment writer final flush failed: %s", self.stream_id, exc
                        )

            except av.error.FFmpegError as exc:
                logger.warning("[%s] PyAV stream error: %s", self.stream_id, exc)
            except Exception as exc:  # noqa: BLE001 - keep the loop resilient
                logger.warning("[%s] stream loop error: %s", self.stream_id, exc)
            finally:
                for container in (relay_output, segment_output, input_container):
                    if container is not None:
                        try:
                            container.close()
                        except Exception:  # noqa: BLE001
                            pass

            if not self._running:
                break

            with self._lock:
                if do_relay:
                    self.health.publishing = False
                self.health.reconnect_count += 1
            logger.info("[%s] reconnecting stream in %.0fs ...", self.stream_id, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 1.5, max_backoff)

        logger.info("Stream loop exited for stream '%s'", self.stream_id)

    def _infer_worker(self) -> None:
        """Caption the most recent sampled frame, off the packet-draining path.

        The VLM ``generate`` call blocks for seconds on CPU; running it here
        instead of inside the sampler keeps the source socket drained the whole
        time, so inference bursts never stall the relay.
        """
        from backend.vlm import get_vlm_engine
        from backend.vlm import parse_yes_no

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

            if settings.DEEP_ANALYZER_ENABLED and self.alert_event and parse_yes_no(caption) and frame_id is not None:
                segment_path = self.frame_registry.get_segment(frame_id) if self.frame_registry else None
                if segment_path:
                    from backend.deep_analyzer import get_deep_analyzer

                    try:
                        get_deep_analyzer().submit(self.stream_id, segment_path, self.alert_event, frame_id)
                    except Exception as exc:  # noqa: BLE001 - model may be missing/misconfigured
                        logger.error("[%s] deep analyzer disabled: %s", self.stream_id, exc)
                else:
                    logger.warning(
                        "[%s] 'Yes' verdict for frame_id=%s but no segment on record", self.stream_id, frame_id
                    )

            inference_count += 1
            if settings.VLM_MAX_INFERENCES and inference_count >= settings.VLM_MAX_INFERENCES:
                logger.info(
                    "[%s] VLM_MAX_INFERENCES=%d reached; inference worker stopping (relay/segments unaffected)",
                    self.stream_id,
                    settings.VLM_MAX_INFERENCES,
                )
                break

        logger.info("Inference worker exited for stream '%s'", self.stream_id)

    def _reclaim_old_segments(self, finalized_segment_path: str) -> None:
        """Roll the per-stream segment buffer: drop the oldest once over MAX_SEGMENTS.

        This is what makes SEGMENT_MAX_ON_DISK a rolling buffer rather than a hard
        stop — every time a segment finalizes, the oldest surviving segment
        is deleted once the retained count exceeds SEGMENT_MAX_ON_DISK, so the
        writer keeps running indefinitely while disk usage stays bounded.
        Only ever called with a segment that has already rotated out (never
        the one ``ffmpeg`` is still writing), so this can't delete an open file.
        """
        if not settings.SEGMENT_MAX_ON_DISK:
            return
        self._finalized_segments.append(finalized_segment_path)
        while len(self._finalized_segments) > settings.SEGMENT_MAX_ON_DISK:
            self._delete_segment(self._finalized_segments.pop(0))

    def _notify_segment_finalized(self, finalized_segment_path: str) -> None:
        """Tell the deep analyzer this segment has rotated out and is safe to read."""
        if not settings.DEEP_ANALYZER_ENABLED:
            return

        try:
            get_deep_analyzer().on_segment_finalized(finalized_segment_path)
        except Exception as exc:  # noqa: BLE001 - model may be missing/misconfigured
            logger.error("[%s] deep analyzer disabled: %s", self.stream_id, exc)

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
