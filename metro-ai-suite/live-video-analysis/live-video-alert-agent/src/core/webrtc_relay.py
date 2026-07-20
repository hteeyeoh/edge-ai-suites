# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""WebRTC relay helper.

Publishes each configured source stream into MediaMTX using ffmpeg so the
frontend can subscribe via WebRTC path /<stream_id>.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)


class WebRTCRelayManager:
    """Manage ffmpeg relay processes keyed by stream_id."""

    def __init__(self, relay_base_url: str, enabled: bool = True):
        self.enabled = enabled
        self.relay_base_url = relay_base_url.rstrip("/")
        self._processes: Dict[str, subprocess.Popen] = {}
        self._workers: Dict[str, Tuple[threading.Thread, threading.Event]] = {}
        self._lock = threading.Lock()
        self._restart_delay_sec = 2.0

    def _target_url(self, stream_id: str) -> str:
        return f"{self.relay_base_url}/{stream_id}"

    def _build_cmd(self, source_url: str, target_url: str) -> List[str]:
        return [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-rtsp_transport",
            "tcp",
            "-i",
            source_url,
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            "copy",
            "-f",
            "rtsp",
            "-rtsp_transport",
            "tcp",
            target_url,
        ]

    def _terminate_process(self, proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

    def _run_worker(self, stream_id: str, source_url: str, stop_event: threading.Event) -> None:
        target_url = self._target_url(stream_id)

        while not stop_event.is_set():
            try:
                proc = subprocess.Popen(
                    self._build_cmd(source_url, target_url),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError:
                logger.error("ffmpeg not found; disable WEBRTC_AUTO_PUBLISH or install ffmpeg")
                return
            except Exception as exc:
                logger.error("Failed to start WebRTC relay for '%s': %s", stream_id, exc)
                if stop_event.wait(self._restart_delay_sec):
                    return
                continue

            with self._lock:
                self._processes[stream_id] = proc

            logger.info("WebRTC relay started for stream '%s' -> %s", stream_id, target_url)

            while proc.poll() is None and not stop_event.wait(0.5):
                pass

            if stop_event.is_set():
                self._terminate_process(proc)

            return_code = proc.poll()
            with self._lock:
                if self._processes.get(stream_id) is proc:
                    self._processes.pop(stream_id, None)

            if stop_event.is_set():
                logger.info("WebRTC relay stopped for stream '%s'", stream_id)
                return

            logger.warning(
                "WebRTC relay exited for stream '%s' with code %s; restarting",
                stream_id,
                return_code,
            )
            if stop_event.wait(self._restart_delay_sec):
                return

    def start(self, stream_id: str, source_url: str) -> None:
        """Start or restart a supervised relay worker for one stream."""
        if not self.enabled:
            return

        self.stop(stream_id)

        stop_event = threading.Event()
        worker = threading.Thread(
            target=self._run_worker,
            args=(stream_id, source_url, stop_event),
            daemon=True,
            name=f"relay-{stream_id[:24]}",
        )
        with self._lock:
            self._workers[stream_id] = (worker, stop_event)
        worker.start()

    def stop(self, stream_id: str) -> None:
        """Stop relay worker and process for one stream."""
        worker: threading.Thread | None = None
        stop_event: threading.Event | None = None
        proc: subprocess.Popen | None = None

        with self._lock:
            pair = self._workers.pop(stream_id, None)
            if pair is not None:
                worker, stop_event = pair
            proc = self._processes.get(stream_id)

        if stop_event is not None:
            stop_event.set()

        if proc is not None:
            self._terminate_process(proc)

        if worker is not None and worker.is_alive():
            worker.join(timeout=4)

        with self._lock:
            self._processes.pop(stream_id, None)

    def stop_all(self) -> None:
        """Stop all relay processes."""
        with self._lock:
            stream_ids = list(self._workers.keys())
        for stream_id in stream_ids:
            self.stop(stream_id)
