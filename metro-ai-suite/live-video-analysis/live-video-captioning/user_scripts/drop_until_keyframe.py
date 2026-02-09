
# -*- coding: utf-8 -*-
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst

class DropUntilKeyframe(object):
    def __init__(self, **kwargs):
        # Optional warm-up frames to discard regardless of flags (default 0)
        self.warmup_n = int(kwargs.get("warmup_n", 0))
        self.count = 0
        self.seen_clean = False

    def _buf(self, frame):
        # Your build stores the Gst.Buffer at this private attribute
        return getattr(frame, "_VideoFrame__buffer", None)

    def _is_clean(self, buf):
        # Normalize flags (tuple vs enum)
        flags = buf.get_flags()
        if isinstance(flags, tuple):
            flags = flags[0]

        bad = (
            (flags & Gst.BufferFlags.DELTA_UNIT) or
            (flags & Gst.BufferFlags.CORRUPTED) or
            (flags & Gst.BufferFlags.DISCONT) or
            (flags & Gst.BufferFlags.GAP)
        )

        # Consider PTS validity; some early buffers have PTS == -1 (GST_CLOCK_TIME_NONE)
        pts = buf.pts
        pts_invalid = (pts is None) or (pts < 0)

        return (not bad) and (not pts_invalid)

    def process(self, frame):
        # Warm-up drop regardless of flags
        if self.count < self.warmup_n:
            self.count += 1
            return False

        buf = self._buf(frame)
        if buf is None:
            # If we cannot access the buffer, fail-open to avoid stalling
            return True

        if not self.seen_clean:
            if self._is_clean(buf):
                self.seen_clean = True
                return True
            else:
                return False
        return True
