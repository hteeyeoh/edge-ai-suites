# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""OpenVINO GenAI vision-language captioning engine.

A single :class:`VLMEngine` wraps one ``openvino_genai.VLMPipeline``. The model
is heavy and not safe to call concurrently, so all requests are served one at a
time by a single dispatcher thread from a priority queue: alert-driven streams
are served ahead of passive ones, FIFO within each tier, instead of racing for
a lock with no ordering guarantee.

The model tree is laid out per device as
``<VLM_MODELS_DIR>/<device>/<VLM_MODEL>`` (e.g. ``/models/cpu/InternVL2-1B``),
matching how the models are mounted into the container.
"""

from __future__ import annotations

import itertools
import logging
import os
import queue
import re
import threading
import time
from typing import Any
from typing import Optional

import numpy as np
import openvino as ov
import openvino_genai as ov_genai

from ..config import settings
from . import utils

logger = logging.getLogger(__name__)


def parse_yes_no(caption: str) -> Optional[bool]:
    """Parse a binary alert-verdict caption (see ALERT_PROMPT_TEMPLATE) into a bool.

    Returns None if the caption doesn't clearly start with "yes"/"no" (e.g. an
    unexpected/free-form response), so callers can distinguish "no event" from
    "couldn't tell".
    """
    normalized = re.sub(r"[^a-z0-9]", "", str(caption or "").strip().lower())
    if normalized.startswith("yes"):
        return True
    if normalized.startswith("no"):
        return False
    return None


class _CaptionRequest:
    """One queued captioning request; the submitting thread blocks on `event`."""

    __slots__ = ("rgb_frame", "prompt", "event", "result", "error")

    def __init__(self, rgb_frame: np.ndarray, prompt: str) -> None:
        self.rgb_frame = rgb_frame
        self.prompt = prompt
        self.event = threading.Event()
        self.result: Optional[tuple[str, dict[str, Optional[float]]]] = None
        self.error: Optional[BaseException] = None


class VLMEngine:
    """Thread-safe wrapper around an OpenVINO GenAI VLM pipeline."""

    def __init__(self) -> None:
        self._pipe = None
        self._gen_config = None
        self._model_path = os.path.join(
            settings.VLM_MODELS_DIR,
            settings.ALERT_VLM_DEVICE.lower(),
            settings.ALERT_VLM_MODEL,
        )
        self._load()
        self._metrics_debug_logged = False

        # Priority queue of (rank, seq, request): rank 0 = alert-priority lane,
        # rank 1 = normal lane; seq preserves FIFO order within a lane.
        self._queue: "queue.PriorityQueue[tuple[int, int, _CaptionRequest]]" = queue.PriorityQueue()
        self._seq_counter = itertools.count()
        self._dispatch_thread = threading.Thread(
            target=self._dispatch_loop, name="vlm-dispatch", daemon=True
        )
        self._dispatch_thread.start()

    def _load(self) -> None:
        # Imported lazily so the app still starts if GenAI is unavailable.
        if not os.path.isdir(self._model_path):
            raise FileNotFoundError(f"VLM model not found at '{self._model_path}'")

        logger.info(
            "Loading VLM '%s' on %s from %s",
            settings.ALERT_VLM_MODEL,
            settings.ALERT_VLM_DEVICE,
            self._model_path,
        )
        pipeline_config = None
        if str(settings.ALERT_VLM_DEVICE).upper() == "NPU":
            pipeline_config = {
                "MAX_PROMPT_LEN": settings.NPU_MAX_PROMPT_LEN,
                "MIN_RESPONSE_LEN": settings.NPU_MIN_RESPONSE_LEN,
            }

        if pipeline_config is None:
            self._pipe = ov_genai.VLMPipeline(self._model_path, settings.ALERT_VLM_DEVICE)
        else:
            self._pipe = ov_genai.VLMPipeline(
                self._model_path,
                settings.ALERT_VLM_DEVICE,
                **pipeline_config,
            )
        self._gen_config = ov_genai.GenerationConfig()
        self._gen_config.max_new_tokens = settings.ALERT_VLM_MAX_TOKENS
        logger.info("VLM pipeline ready")

    @staticmethod
    def _extract_caption_text(result: Any) -> str:
        # GenAI result often carries `texts`; fall back to string conversion.
        texts = getattr(result, "texts", None)
        if isinstance(texts, (list, tuple)) and texts:
            return str(texts[0]).strip()
        return str(result).strip()

    def _dispatch_loop(self) -> None:
        """Single worker thread; the only caller of `_generate`, so no lock is needed there."""
        while True:
            _rank, _seq, request = self._queue.get()
            try:
                request.result = self._generate(request.rgb_frame, request.prompt)
            except Exception as exc:  # noqa: BLE001
                request.error = exc
            finally:
                request.event.set()

    def _generate(
        self, rgb_frame: np.ndarray, prompt_text: str
    ) -> tuple[str, dict[str, Optional[float]]]:
        """Call the GenAI VLM pipeline on one frame and prompt."""
        # GenAI expects a batched NHWC uint8 tensor.
        tensor = ov.Tensor(np.expand_dims(rgb_frame, axis=0))
        t0 = time.perf_counter()
        result = self._pipe.generate(
            prompt_text,
            images=[tensor],
            generation_config=self._gen_config,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        metrics = utils._extract_perf_metrics(result)
        if metrics.get("ttft_ms") is None and not self._metrics_debug_logged:
            self._metrics_debug_logged = True
            logger.warning(
                "VLM result has no usable perf_metrics; falling back to estimated metrics"
            )

        caption_text = self._extract_caption_text(result)
        return caption_text, metrics

    def caption_with_metrics(
        self,
        rgb_frame: np.ndarray,
        prompt: Optional[str] = None,
        *,
        priority: bool = False,
    ) -> tuple[str, dict[str, Optional[float]]]:
        """Queue a captioning request and block until served.

        `priority=True` (e.g. alert-driven streams) is served ahead of normal
        requests; ordering within each tier is FIFO.
        """
        prompt_text = (prompt or "").strip()
        if not prompt_text:
            raise ValueError("prompt is required")

        request = _CaptionRequest(rgb_frame, prompt_text)
        rank = 0 if priority else 1
        self._queue.put((rank, next(self._seq_counter), request))
        request.event.wait()

        if request.error is not None:
            raise request.error
        assert request.result is not None
        return request.result

    def caption(self, rgb_frame: np.ndarray, prompt: Optional[str] = None) -> str:
        """Return a caption for one RGB frame (H×W×3, uint8)."""
        caption_text, _metrics = self.caption_with_metrics(rgb_frame, prompt=prompt)
        return caption_text


_engine: Optional[VLMEngine] = None
_engine_lock = threading.Lock()


def get_vlm_engine() -> VLMEngine:
    """Return the shared VLM engine, loading it once on first use."""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = VLMEngine()
    return _engine
