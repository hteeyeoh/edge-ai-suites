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

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

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


class StreamManager:
    """Relay a single video source into MediaMTX with PyAV."""

    def __init__(self, stream_id: str, source_url: str):
        self.stream_id = stream_id
        self.source_url = source_url
        self.target_url = f"{settings.WEBRTC_RELAY_URL.rstrip('/')}/{stream_id}"

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.health = StreamHealth()

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

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
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
            )

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _input_options(self) -> dict:
        if str(self.source_url).startswith(("rtsp://", "rtsps://")):
            return {
                "rtsp_transport": settings.RTSP_TRANSPORT,
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

                for packet in input_container.demux(in_stream):
                    if not self._running:
                        break
                    if packet.dts is None:
                        continue  # skip non-timed packets (e.g. header flushes)

                    packet.stream = out_stream
                    output_container.mux(packet)

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
