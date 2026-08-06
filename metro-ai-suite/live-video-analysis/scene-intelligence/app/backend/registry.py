# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""In-memory registry of active :class:`StreamManager` instances."""

from __future__ import annotations

import logging
import threading
from typing import Dict, List

from backend.config import settings
from backend.frame_registry import SegmentFrameRegistry
from backend.stream_manager import StreamManager

logger = logging.getLogger(__name__)


class StreamRegistry:
    """Thread-safe registry that owns the lifecycle of every stream."""

    def __init__(self, frame_registry: SegmentFrameRegistry) -> None:
        self._streams: Dict[str, StreamManager] = {}
        self._lock = threading.Lock()
        self.frame_registry = frame_registry

    def add(
        self,
        stream_id: str,
        source_url: str,
        vlm_prompt: str = "",
        alert_event: str = "",
    ) -> StreamManager:
        if not source_url:
            raise ValueError("source_url must not be empty")
        with self._lock:
            if stream_id in self._streams:
                raise ValueError(f"Stream '{stream_id}' already exists")
            if len(self._streams) >= settings.MAX_STREAMS:
                raise ValueError(f"Maximum of {settings.MAX_STREAMS} streams reached")
            manager = StreamManager(
                stream_id,
                source_url,
                vlm_prompt,
                alert_event,
                frame_registry=self.frame_registry,
            )
            self._streams[stream_id] = manager
        manager.start()
        return manager

    def remove(self, stream_id: str) -> None:
        with self._lock:
            manager = self._streams.pop(stream_id, None)
        if manager is None:
            raise KeyError(stream_id)
        manager.stop()

    def ids(self) -> List[str]:
        with self._lock:
            return list(self._streams.keys())

    def all(self) -> List[StreamManager]:
        with self._lock:
            return list(self._streams.values())

    def stop_all(self) -> None:
        for manager in self.all():
            manager.stop()
        with self._lock:
            self._streams.clear()
