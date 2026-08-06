# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Bounded, thread-safe metadata index mapping sampled frames to segment files.

Each :class:`StreamManager` writes rolling ``.mp4`` segments to disk and, at a
lower rate, registers metadata for the frames it samples for VLM/detector
consumption. The registry lets a downstream deep analyzer resolve a
``frame_id`` — surfaced by a fast detector — back to the segment file that
contains it, without persisting any frame image data.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class FrameRecord:
    frame_id: uuid.UUID
    stream_id: str
    rtsp_url: str
    segment_path: str
    pts_seconds: Optional[float] = None
    created_ts: float = field(default_factory=time.time)


class SegmentFrameRegistry:
    """Thread-safe bounded registry keyed by UUID; evicts oldest on overflow."""

    def __init__(self, max_records: int = 3000):
        self._records: Dict[uuid.UUID, FrameRecord] = {}
        self._lock = threading.Lock()
        self._max_records = max_records

    def register(self, record: FrameRecord) -> None:
        with self._lock:
            self._records[record.frame_id] = record
            if len(self._records) > self._max_records:
                # plain dict preserves insertion order since Python 3.7
                del self._records[next(iter(self._records))]

    def get_record(self, frame_id: uuid.UUID) -> Optional[FrameRecord]:
        with self._lock:
            return self._records.get(frame_id)

    def get_segment(self, frame_id: uuid.UUID) -> Optional[str]:
        """Return the segment .mp4 that contains this frame."""
        record = self.get_record(frame_id)
        return record.segment_path if record else None

    def remove_segment(self, stream_id: str, segment_path: str) -> int:
        """Purge every record for a reclaimed segment file. Returns the count removed."""
        with self._lock:
            stale = [
                frame_id
                for frame_id, record in self._records.items()
                if record.stream_id == stream_id and record.segment_path == segment_path
            ]
            for frame_id in stale:
                del self._records[frame_id]
            return len(stale)


    def latest(self, stream_id: Optional[str] = None, limit: int = 50) -> List[FrameRecord]:
        """Return the most recently registered records, optionally filtered by stream."""
        with self._lock:
            records = list(self._records.values())
        if stream_id is not None:
            records = [r for r in records if r.stream_id == stream_id]
        return records[-limit:]

    def stats(self) -> dict:
        with self._lock:
            total = len(self._records)
            per_stream: Dict[str, int] = {}
            for record in self._records.values():
                per_stream[record.stream_id] = per_stream.get(record.stream_id, 0) + 1
        return {
            "total": total,
            "capacity": self._max_records,
            "per_stream": per_stream,
        }
