# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Multi-frame deep-analysis follow-up for segments with a "Yes" alert verdict.

Separate from ``vlm.py``'s single-frame captioning engine: different model
(video-capable, e.g. Qwen3.5), different input (several frames sampled from a
finalized ``.mp4`` segment instead of one live frame) and different cadence
(once per confirmed-alert segment instead of every ``VLM_INTERVAL``).

Flow, driven by :class:`backend.stream_manager.StreamManager`:

1. ``submit()`` is called the moment a sampled frame's caption comes back
   "Yes". It resolves to a ``segment_path`` (via the frame registry) and is
   deduped so a segment is only ever analyzed once, however many "Yes" frames
   land in it.
2. Because the segment may still be the one actively being written by
   ffmpeg, the job is only actually enqueued for analysis once
   ``on_segment_finalized()`` reports that segment has rotated out (called by
   the segment writer loop at rotation time). If the finalize signal already
   arrived first, ``submit()`` enqueues immediately. That signal is only a
   prediction though, so before actually reading the file the worker also
   waits for the *next* segment to appear on disk — proof the muxer has moved
   on and this one's trailer is flushed, regardless of GOP/keyframe timing.
3. A single background dispatcher thread runs the heavy video-VLM call,
    logs the resulting description, and hands upload work to the object
    storage helper when enabled.
"""

from __future__ import annotations

import logging
import os
import queue
import re
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional

import av
import numpy as np
from backend import utils
from backend.config import settings
from backend.object_storage import SeaweedFSStorage

logger = logging.getLogger(__name__)

_SEGMENT_INDEX_RE = re.compile(r"(\d+)(?=\.[^.]+$)")


def _next_segment_path(segment_path: str) -> Optional[str]:
    """Return the path of the segment that immediately follows `segment_path`.

    The muxer only creates this file once the current one is fully closed, so
    its existence is a ground-truth "safe to read" signal, independent of any
    GOP/keyframe timing assumptions.
    """
    match = _SEGMENT_INDEX_RE.search(segment_path)
    if not match:
        return None
    digits = match.group(1)
    next_digits = str(int(digits) + 1).zfill(len(digits))
    start, end = match.span(1)
    next_path = segment_path[:start] + next_digits + segment_path[end:]
    logger.debug("next segment path for %s -> %s", segment_path, next_path)
    return next_path


@dataclass
class _AnalysisJob:
    stream_id: str
    segment_path: str
    alert_event: str
    frame_id: uuid.UUID


def _sample_segment_frames(segment_path: str, max_frames: int) -> "np.ndarray":
    """Uniformly sample up to `max_frames` frames from a finalized .mp4 segment.

    Mirrors the sampling approach used for live-source frame registration
    (uniform indices over the total frame count), via PyAV — already a
    project dependency — instead of introducing OpenCV.
    """

    container = av.open(segment_path)
    try:
        stream = container.streams.video[0]
        total_frames = stream.frames or 0
        frames: list[np.ndarray] = []

        if total_frames > 0:
            sample_count = min(max_frames, total_frames)
            indices = set(np.arange(0, total_frames, total_frames / sample_count).astype(int).tolist())
            idx = 0
            for frame in container.decode(stream):
                if idx in indices:
                    frames.append(frame.to_ndarray(format="bgr24"))
                idx += 1
        else:
            # Frame count not available from container metadata; decode fully.
            for frame in container.decode(stream):
                frames.append(frame.to_ndarray(format="bgr24"))
            if len(frames) > max_frames:
                indices = np.linspace(0, len(frames) - 1, max_frames).astype(int)
                frames = [frames[i] for i in indices]
    finally:
        container.close()

    if not frames:
        raise ValueError(f"No frames could be decoded from segment: {segment_path}")
    return np.stack(frames)


class DeepAnalyzerEngine:
    """Thread-safe wrapper around a video-capable OpenVINO GenAI VLM pipeline."""

    def __init__(self) -> None:
        self._pipe = None
        self._gen_config = None
        self._load()

        self._lock = threading.Lock()
        # Segment paths already submitted (queued, pending, or done) — the
        # "don't re-analyze this segment" marker. Bounded FIFO eviction.
        self._dedup: "OrderedDict[str, None]" = OrderedDict()
        # Segment paths confirmed finalized (rotated out by the writer) but
        # with no pending job for them (yet, or ever). Bounded FIFO eviction.
        self._finalized: "OrderedDict[str, None]" = OrderedDict()
        # Jobs whose "Yes" verdict arrived before their segment finalized.
        self._pending: dict[str, _AnalysisJob] = {}

        self._queue: "queue.Queue[_AnalysisJob]" = queue.Queue()
        self._dispatch_thread = threading.Thread(
            target=self._dispatch_loop, name="deep-analyzer-dispatch", daemon=True
        )
        self._dispatch_thread.start()

        self._object_storage: SeaweedFSStorage = SeaweedFSStorage()

    def _load(self) -> None:
        import openvino_genai as ov_genai

        model_path = os.path.join(
            settings.DEEP_ANALYZER_MODELS_DIR,
            settings.DEEP_ANALYZER_DEVICE.lower(),
            settings.DEEP_ANALYZER_MODEL,
        )
        if not os.path.isdir(model_path):
            raise FileNotFoundError(f"Deep analyzer model not found at '{model_path}'")

        logger.info(
            "Loading deep analyzer '%s' on %s from %s",
            settings.DEEP_ANALYZER_MODEL,
            settings.DEEP_ANALYZER_DEVICE,
            model_path,
        )
        pipeline_config = None
        vlm_cache_dir = os.path.join(tempfile.gettempdir(), settings.DEEP_ANALYZER_DEVICE.lower(), "vlm_cache")
        os.makedirs(vlm_cache_dir, exist_ok=True)
        if str(settings.DEEP_ANALYZER_DEVICE).upper() == "NPU":
            pipeline_config = {
                "MAX_PROMPT_LEN": settings.DEEP_ANALYZER_NPU_MAX_PROMPT_LEN,
                "MIN_RESPONSE_LEN": settings.DEEP_ANALYZER_NPU_MIN_RESPONSE_LEN,
            }

        if pipeline_config is None:
            self._pipe = ov_genai.VLMPipeline(model_path, settings.DEEP_ANALYZER_DEVICE, **{"CACHE_DIR": vlm_cache_dir})
        else:
            self._pipe = ov_genai.VLMPipeline(model_path, settings.DEEP_ANALYZER_DEVICE, **pipeline_config)
        self._gen_config = ov_genai.GenerationConfig()
        self._gen_config.max_new_tokens = settings.DEEP_ANALYZER_MAX_TOKENS
        logger.info("Deep analyzer pipeline ready")

    # ------------------------------------------------------------------ #
    # Bounded set helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _mark(cache: "OrderedDict[str, None]", key: str) -> None:
        cache[key] = None
        if len(cache) > settings.DEEP_ANALYZER_DEDUP_CACHE_SIZE:
            cache.popitem(last=False)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def submit(self, stream_id: str, segment_path: str, alert_event: str, frame_id: uuid.UUID) -> None:
        """Register a "Yes"-verdict segment for deep analysis, at most once."""
        job = _AnalysisJob(stream_id, segment_path, alert_event, frame_id)
        with self._lock:
            if segment_path in self._dedup:
                return  # already queued/pending/done — don't waste compute
            self._mark(self._dedup, segment_path)

            if segment_path not in self._finalized:
                # Still being written; hold until on_segment_finalized() fires.
                self._pending[segment_path] = job
                logger.info(
                    "[%s] deep-analysis deferred (segment still open): %s",
                    stream_id,
                    segment_path,
                )
                return

        self._queue.put(job)
        logger.info("[%s] deep-analysis queued: %s", stream_id, segment_path)

    def on_segment_finalized(self, segment_path: str) -> None:
        """Signal that a segment has rotated out and is now safe to read."""
        with self._lock:
            self._mark(self._finalized, segment_path)
            job = self._pending.pop(segment_path, None)
        if job is not None:
            self._queue.put(job)
            logger.info("[%s] deep-analysis queued (segment finalized): %s", job.stream_id, segment_path)

    # ------------------------------------------------------------------ #
    # Worker
    # ------------------------------------------------------------------ #

    def _dispatch_loop(self) -> None:
        while True:
            job = self._queue.get()
            try:
                self._analyze(job)
            except Exception:  # noqa: BLE001 - keep the dispatcher alive
                logger.exception(
                    "[%s] deep-analysis failed for segment=%s", job.stream_id, job.segment_path
                )

    def _read_segment_frames(self, job: _AnalysisJob) -> np.ndarray:
        """Read frames from a "finalized" segment, tolerating a not-yet-flushed trailer.

        The finalize signal is a prediction, not a confirmed file-close (see
        module docstring / StreamManager._notify_segment_finalized), so this
        first waits for ground-truth proof (the next segment existing) before
        even trying, then still tolerates a residual open-file race with retries.
        """
        self._wait_for_next_segment(job)

        max_retries = settings.DEEP_ANALYZER_SEGMENT_READ_MAX_RETRIES
        for attempt in range(1, max_retries + 1):
            try:
                return _sample_segment_frames(job.segment_path, settings.DEEP_ANALYZER_MAX_FRAMES)
            except Exception as exc:  # noqa: BLE001 - av raises FFmpegError/OSError/ValueError depending on how incomplete the file is
                if attempt >= max_retries:
                    logger.error(
                        "[%s] SEGMENT MISSED (deep analysis skipped): %s gave up after %d attempts: %s",
                        job.stream_id,
                        job.segment_path,
                        max_retries,
                        exc,
                    )
                    raise
                logger.warning(
                    "[%s] segment not readable yet (attempt %d/%d), retrying: %s | segment=%s",
                    job.stream_id,
                    attempt,
                    max_retries,
                    exc,
                    job.segment_path,
                )
                time.sleep(settings.DEEP_ANALYZER_SEGMENT_READ_RETRY_DELAY)
        raise AssertionError("unreachable")  # loop always returns or raises

    def _wait_for_next_segment(self, job: _AnalysisJob) -> None:
        """Block until the *next* segment file appears, proving this one is finalized.

        Falls back to just proceeding (relying on the read-retry loop instead)
        if the next segment's path can't be derived or never shows up.
        """
        next_path = _next_segment_path(job.segment_path)
        if next_path is None:
            return

        max_retries = settings.DEEP_ANALYZER_SEGMENT_READ_MAX_RETRIES
        for attempt in range(1, max_retries + 1):
            if os.path.exists(next_path):
                return
            if attempt >= max_retries:
                logger.warning(
                    "[%s] next segment %s hasn't appeared after %d attempts; trying %s anyway",
                    job.stream_id,
                    next_path,
                    max_retries,
                    job.segment_path,
                )
                return
            time.sleep(settings.DEEP_ANALYZER_SEGMENT_READ_RETRY_DELAY)

    def _analyze(self, job: _AnalysisJob) -> None:
        import openvino as ov

        frames = self._read_segment_frames(job)
        tensor = ov.Tensor(frames)
        prompt = settings.DEEP_ANALYZER_PROMPT_TEMPLATE.format(event=job.alert_event)

        logger.info(
            "[%s] deep-analyzing segment=%s frame_id=%s (%d frames)",
            job.stream_id,
            job.segment_path,
            job.frame_id,
            frames.shape[0],
        )
        t0 = time.perf_counter()
        result = self._pipe.generate(prompt, videos=[tensor], generation_config=self._gen_config)
        total_duration_ms = (time.perf_counter() - t0) * 1000.0

        texts = getattr(result, "texts", None)
        description = str(texts[0]).strip() if isinstance(texts, (list, tuple)) and texts else str(result).strip()

        metrics = utils._extract_perf_metrics(result)
        metrics["total_duration_ms"] = total_duration_ms
        metrics["frames_sampled"] = float(frames.shape[0])
        metrics["tensor_shape"] = str(frames.shape)
        metrics["tensor_dtype"] = str(frames.dtype)
        metrics["segment_path"] = str(job.segment_path)

        logger.info(
            "[%s] deep-analysis result segment=%s frame_id=%s: %s",
            job.stream_id,
            job.segment_path,
            job.frame_id,
            description,
        )
        logger.info(
            "[%s] deep-analysis metrics segment=%s frame_id=%s: %s",
            job.stream_id,
            job.segment_path,
            job.frame_id,
            metrics,
        )

        if self._object_storage is not None:
            self._object_storage.upload_segment_and_metadata(
                stream_id=job.stream_id,
                segment_path=job.segment_path,
                alert_event=job.alert_event,
                frame_id=job.frame_id,
                description=description,
                metrics=metrics,
                deep_model=settings.DEEP_ANALYZER_MODEL,
                deep_device=settings.DEEP_ANALYZER_DEVICE,
            )


_engine: Optional[DeepAnalyzerEngine] = None
_engine_lock = threading.Lock()


def get_deep_analyzer() -> DeepAnalyzerEngine:
    """Return the shared deep-analyzer engine, loading it once on first use."""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = DeepAnalyzerEngine()
    return _engine
