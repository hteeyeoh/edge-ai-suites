# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""OpenVINO GenAI vision-language captioning engine.

A single :class:`VLMEngine` wraps one ``openvino_genai.VLMPipeline``. The model
is heavy and not safe to call concurrently, so :meth:`caption` serialises every
request behind a lock — multiple stream workers share one loaded pipeline.

The model tree is laid out per device as
``<VLM_MODELS_DIR>/<device>/<VLM_MODEL>`` (e.g. ``/models/cpu/InternVL2-1B``),
matching how the models are mounted into the container.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any, Optional

import numpy as np

from backend.config import settings

logger = logging.getLogger(__name__)


class VLMEngine:
    """Thread-safe wrapper around an OpenVINO GenAI VLM pipeline."""

    def __init__(self) -> None:
        self._pipe = None
        self._gen_config = None
        self._lock = threading.Lock()
        self._model_path = os.path.join(
            settings.VLM_MODELS_DIR,
            settings.VLM_DEVICE.lower(),
            settings.VLM_MODEL,
        )
        self._load()
        self._metrics_debug_logged = False

    def _load(self) -> None:
        # Imported lazily so the app still starts if GenAI is unavailable.
        import openvino_genai as ov_genai

        if not os.path.isdir(self._model_path):
            raise FileNotFoundError(f"VLM model not found at '{self._model_path}'")

        logger.info(
            "Loading VLM '%s' on %s from %s",
            settings.VLM_MODEL,
            settings.VLM_DEVICE,
            self._model_path,
        )
        pipeline_config = None
        if str(settings.VLM_DEVICE).upper() == "NPU":
            pipeline_config = {
                "MAX_PROMPT_LEN": settings.NPU_MAX_PROMPT_LEN,
                "MIN_RESPONSE_LEN": settings.NPU_MIN_RESPONSE_LEN,
            }

        if pipeline_config is None:
            self._pipe = ov_genai.VLMPipeline(self._model_path, settings.VLM_DEVICE)
        else:
            self._pipe = ov_genai.VLMPipeline(
                self._model_path,
                settings.VLM_DEVICE,
                **pipeline_config,
            )
        self._gen_config = ov_genai.GenerationConfig()
        self._gen_config.max_new_tokens = settings.VLM_MAX_TOKENS
        logger.info("VLM pipeline ready")

    @staticmethod
    def _as_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            pass

        # Fallback for values represented as strings with units (e.g. "12.3 ms").
        if isinstance(value, str):
            match = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", value)
            if match:
                try:
                    return float(match.group(0))
                except ValueError:
                    return None

        # Fallback for duration-like objects.
        if hasattr(value, "total_seconds") and callable(getattr(value, "total_seconds")):
            try:
                return float(value.total_seconds())
            except Exception:  # noqa: BLE001
                return None

        return None

    @staticmethod
    def _normalize_token(text: str) -> str:
        return re.sub(r"[^a-z0-9]", "", str(text).lower())

    @staticmethod
    def _extract_caption_text(result: Any) -> str:
        # GenAI result often carries `texts`; fall back to string conversion.
        texts = getattr(result, "texts", None)
        if isinstance(texts, (list, tuple)) and texts:
            return str(texts[0]).strip()
        return str(result).strip()

    @staticmethod
    def _is_truthy_text(value: str) -> bool:
        normalized = str(value or "").strip().lower()
        return normalized in {"1", "true", "yes", "y", "detected", "present"}

    @classmethod
    def _fallback_alert_payload(cls, raw_text: str, alert_event: str) -> dict[str, Any]:
        text = str(raw_text or "").strip()
        lowered = text.lower()
        triggered = False
        if cls._is_truthy_text(text):
            triggered = True
        elif re.search(r"\b(yes|true|present|detected)\b", lowered):
            triggered = True
        elif re.search(r"\b(no|false|absent|not\s+present|not\s+detected)\b", lowered):
            triggered = False

        return {
            "alerts": [
                {
                    "event": str(alert_event or "").strip(),
                    "triggered": bool(triggered),
                }
            ]
        }

    @staticmethod
    def _alert_json_schema(alert_event: str) -> dict[str, Any]:
        event_text = str(alert_event or "").strip()
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "alerts": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 1,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "event": {
                                "type": "string",
                                "enum": [event_text],
                            },
                            "triggered": {
                                "type": "boolean",
                            },
                        },
                        "required": ["event", "triggered"],
                    },
                }
            },
            "required": ["alerts"],
        }

    @classmethod
    def _normalize_alert_payload(cls, payload: Any, alert_event: str) -> dict[str, Any]:
        event_text = str(alert_event or "").strip()
        if not isinstance(payload, dict):
            return cls._fallback_alert_payload(str(payload), event_text)

        alerts = payload.get("alerts")
        if not isinstance(alerts, list) or not alerts:
            maybe_triggered = payload.get("triggered")
            if isinstance(maybe_triggered, bool):
                return {
                    "alerts": [
                        {
                            "event": event_text,
                            "triggered": maybe_triggered,
                        }
                    ]
                }
            return cls._fallback_alert_payload(json.dumps(payload), event_text)

        first = alerts[0]
        if not isinstance(first, dict):
            return cls._fallback_alert_payload(json.dumps(payload), event_text)

        raw_event = str(first.get("event") or "").strip() or event_text
        raw_triggered = first.get("triggered")
        triggered = raw_triggered if isinstance(raw_triggered, bool) else cls._is_truthy_text(str(raw_triggered))

        return {
            "alerts": [
                {
                    "event": raw_event,
                    "triggered": bool(triggered),
                }
            ]
        }

    @staticmethod
    def _estimate_token_count(text: str) -> int:
        cleaned = str(text or "").strip()
        if not cleaned:
            return 0
        # Lightweight approximation for fallback metrics.
        return max(1, len(cleaned.split()))

    @staticmethod
    def _fill_fallback_metrics(
        metrics: dict[str, Optional[float]],
        elapsed_ms: float,
        caption_text: str,
    ) -> dict[str, Optional[float]]:
        token_count = VLMEngine._estimate_token_count(caption_text)
        safe_elapsed_ms = max(0.0, float(elapsed_ms))

        if metrics.get("ttft_ms") is None:
            # Conservative fallback: TTFT approximated with full generation latency.
            metrics["ttft_ms"] = safe_elapsed_ms

        inferred_tpot = None
        if token_count > 0:
            inferred_tpot = safe_elapsed_ms / token_count

        if metrics.get("tpot_ms") is None:
            metrics["tpot_ms"] = inferred_tpot

        if metrics.get("throughput_tps") is None:
            tpot_value = metrics.get("tpot_ms")
            if tpot_value is not None and tpot_value > 0:
                metrics["throughput_tps"] = 1000.0 / tpot_value

        return metrics

    @staticmethod
    def _empty_metrics() -> dict[str, Optional[float]]:
        return {
            "ttft_ms": None,
            "tpot_ms": None,
            "throughput_tps": None,
        }

    @staticmethod
    def _metrics_unavailable(metrics: dict[str, Optional[float]]) -> bool:
        return (
            metrics.get("ttft_ms") is None
            and metrics.get("tpot_ms") is None
            and metrics.get("throughput_tps") is None
        )

    @classmethod
    def _walk_numeric_fields(
        cls,
        obj: Any,
        *,
        prefix: str = "",
        depth: int = 0,
        max_depth: int = 4,
        max_items: int = 200,
        seen: Optional[set[int]] = None,
    ) -> list[tuple[str, float]]:
        if obj is None or depth > max_depth:
            return []

        if seen is None:
            seen = set()
        obj_id = id(obj)
        if obj_id in seen:
            return []
        seen.add(obj_id)

        out: list[tuple[str, float]] = []

        direct_value = cls._as_float(obj)
        if direct_value is not None:
            out.append((prefix or "value", direct_value))
            return out

        if isinstance(obj, dict):
            for key, value in obj.items():
                if len(out) >= max_items:
                    break
                key_text = str(key)
                child_prefix = f"{prefix}.{key_text}" if prefix else key_text
                out.extend(
                    cls._walk_numeric_fields(
                        value,
                        prefix=child_prefix,
                        depth=depth + 1,
                        max_depth=max_depth,
                        max_items=max_items - len(out),
                        seen=seen,
                    )
                )
            return out

        if isinstance(obj, (list, tuple, set)):
            for index, value in enumerate(obj):
                if len(out) >= max_items:
                    break
                child_prefix = f"{prefix}[{index}]" if prefix else f"[{index}]"
                out.extend(
                    cls._walk_numeric_fields(
                        value,
                        prefix=child_prefix,
                        depth=depth + 1,
                        max_depth=max_depth,
                        max_items=max_items - len(out),
                        seen=seen,
                    )
                )
            return out

        # Object attributes.
        try:
            attrs = [name for name in dir(obj) if not str(name).startswith("_")]
        except Exception:  # noqa: BLE001
            attrs = []

        for name in attrs:
            if len(out) >= max_items:
                break
            try:
                value = getattr(obj, name)
            except Exception:  # noqa: BLE001
                continue

            if callable(value):
                # Ignore methods that likely require arguments.
                try:
                    value = value()
                except TypeError:
                    continue
                except Exception:  # noqa: BLE001
                    continue

            child_prefix = f"{prefix}.{name}" if prefix else name
            out.extend(
                cls._walk_numeric_fields(
                    value,
                    prefix=child_prefix,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_items=max_items - len(out),
                    seen=seen,
                )
            )

        return out

    @classmethod
    def _extract_metric_from_obj(cls, source: Any, names: tuple[str, ...]) -> Optional[float]:
        if source is None:
            return None

        def resolve_numeric(candidate: Any) -> Optional[float]:
            parsed = cls._as_float(candidate)
            if parsed is not None:
                return parsed

            # Some backends expose statistics objects with mean/avg/value fields.
            if isinstance(candidate, dict):
                for stat_key in ("mean", "avg", "average", "value"):
                    if stat_key in candidate:
                        nested = cls._as_float(candidate.get(stat_key))
                        if nested is not None:
                            return nested
            else:
                for stat_key in ("mean", "avg", "average", "value"):
                    if hasattr(candidate, stat_key):
                        stat_value = getattr(candidate, stat_key)
                        if callable(stat_value):
                            try:
                                stat_value = stat_value()
                            except TypeError:
                                continue
                        nested = cls._as_float(stat_value)
                        if nested is not None:
                            return nested
            return None

        # Dict-like metrics payload.
        if isinstance(source, dict):
            lower_map = {str(k).lower(): v for k, v in source.items()}
            for name in names:
                if name in lower_map:
                    return resolve_numeric(lower_map[name])

        # Object attribute payload.
        for name in names:
            if hasattr(source, name):
                value = getattr(source, name)
                if callable(value):
                    try:
                        value = value()
                    except TypeError:
                        continue
                parsed = resolve_numeric(value)
                if parsed is not None:
                    return parsed
        return None

    @classmethod
    def _extract_perf_metrics_from_pipe(cls, pipe: Any) -> dict[str, Optional[float]]:
        if pipe is None:
            return cls._empty_metrics()

        metrics_obj = None
        for method_name in (
            "get_perf_metrics",
            "get_performance_metrics",
            "get_generation_metrics",
            "get_metrics",
        ):
            if not hasattr(pipe, method_name):
                continue
            method = getattr(pipe, method_name)
            if not callable(method):
                continue
            try:
                metrics_obj = method()
            except Exception:  # noqa: BLE001 - API differs across versions/devices
                metrics_obj = None
            if metrics_obj is not None:
                break

        if metrics_obj is None:
            return cls._empty_metrics()

        return cls._extract_perf_metrics(metrics_obj)

    @staticmethod
    def _debug_attr_names(obj: Any, limit: int = 80) -> list[str]:
        try:
            names = [n for n in dir(obj) if not str(n).startswith("_")]
        except Exception:  # noqa: BLE001
            return []
        return names[:limit]

    @classmethod
    def _extract_perf_metrics(cls, result: Any) -> dict[str, Optional[float]]:
        # Try direct result object, then common nested containers.
        candidates = [result]
        for attr_name in (
            "metrics",
            "perf_metrics",
            "extended_perf_metrics",
            "performance_metrics",
            "generation_metrics",
        ):
            nested = getattr(result, attr_name, None)
            if nested is not None:
                candidates.append(nested)

        ttft_names = (
            "ttft",
            "ttft_ms",
            "time_to_first_token",
            "time_to_first_token_ms",
            "first_token_latency",
            "first_token_latency_ms",
        )
        tpot_names = (
            "tpot",
            "tpot_ms",
            "time_per_output_token",
            "time_per_output_token_ms",
            "token_latency",
            "token_latency_ms",
        )
        throughput_names = (
            "throughput",
            "throughput_tps",
            "tokens_per_second",
            "tok_per_s",
            "tokens_sec",
            "generation_throughput",
            "token_throughput",
        )

        ttft_ms = None
        tpot_ms = None
        throughput_tps = None

        # First pass: direct lookup via common names.
        for candidate in candidates:
            if ttft_ms is None:
                ttft_ms = cls._extract_metric_from_obj(candidate, ttft_names)
            if tpot_ms is None:
                tpot_ms = cls._extract_metric_from_obj(candidate, tpot_names)
            if throughput_tps is None:
                throughput_tps = cls._extract_metric_from_obj(candidate, throughput_names)

        # Second pass: recursive field walk for runtime-specific object shapes.
        if ttft_ms is None or tpot_ms is None or throughput_tps is None:
            ttft_tokens = {cls._normalize_token(name) for name in ttft_names}
            tpot_tokens = {cls._normalize_token(name) for name in tpot_names}
            throughput_tokens = {cls._normalize_token(name) for name in throughput_names}

            for candidate in candidates:
                for path, value in cls._walk_numeric_fields(candidate):
                    normalized_path = cls._normalize_token(path)
                    if ttft_ms is None and any(token in normalized_path for token in ttft_tokens):
                        ttft_ms = value
                    if tpot_ms is None and any(token in normalized_path for token in tpot_tokens):
                        tpot_ms = value
                    if throughput_tps is None and any(token in normalized_path for token in throughput_tokens):
                        throughput_tps = value
                    if ttft_ms is not None and tpot_ms is not None and throughput_tps is not None:
                        break
                if ttft_ms is not None and tpot_ms is not None and throughput_tps is not None:
                    break

        return {
            "ttft_ms": ttft_ms,
            "tpot_ms": tpot_ms,
            "throughput_tps": throughput_tps,
        }

    def caption_with_metrics(
        self,
        rgb_frame: np.ndarray,
        prompt: Optional[str] = None,
    ) -> tuple[str, dict[str, Optional[float]]]:
        """Return caption text and optional latency/throughput metrics."""
        import openvino as ov

        prompt_text = (prompt or "").strip()
        if not prompt_text:
            raise ValueError("prompt is required")

        # GenAI expects a batched NHWC uint8 tensor.
        tensor = ov.Tensor(np.expand_dims(rgb_frame, axis=0))
        with self._lock:
            t0 = time.perf_counter()
            result = self._pipe.generate(
                prompt_text,
                images=[tensor],
                generation_config=self._gen_config,
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            metrics = self._extract_perf_metrics(result)
            if self._metrics_unavailable(metrics):
                metrics = self._extract_perf_metrics_from_pipe(self._pipe)

            if not self._metrics_debug_logged and self._metrics_unavailable(metrics):
                self._metrics_debug_logged = True
                logger.warning(
                    "VLM metrics unavailable from current runtime API | result_attrs=%s | pipe_attrs=%s",
                    self._debug_attr_names(result),
                    self._debug_attr_names(self._pipe),
                )

            caption_text = self._extract_caption_text(result)
            metrics = self._fill_fallback_metrics(metrics, elapsed_ms, caption_text)

        return caption_text, metrics

    def infer_alert_with_metrics(
        self,
        rgb_frame: np.ndarray,
        prompt: Optional[str],
        alert_event: str,
    ) -> tuple[dict[str, Any], dict[str, Optional[float]]]:
        """Return a structured alert payload and optional latency/throughput metrics."""
        import openvino as ov
        import openvino_genai as ov_genai

        prompt_text = (prompt or "").strip()
        event_text = str(alert_event or "").strip()
        if not prompt_text:
            raise ValueError("prompt is required")
        if not event_text:
            raise ValueError("alert_event is required")

        schema = self._alert_json_schema(event_text)

        # Use a dedicated deterministic config for schema-constrained JSON generation.
        gen_config = ov_genai.GenerationConfig()
        gen_config.max_new_tokens = settings.VLM_MAX_TOKENS
        gen_config.do_sample = False
        gen_config.structured_output_config = ov_genai.StructuredOutputConfig(
            json_schema=json.dumps(schema)
        )

        # GenAI expects a batched NHWC uint8 tensor.
        tensor = ov.Tensor(np.expand_dims(rgb_frame, axis=0))
        with self._lock:
            t0 = time.perf_counter()
            result = self._pipe.generate(
                prompt_text,
                images=[tensor],
                generation_config=gen_config,
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            metrics = self._extract_perf_metrics(result)
            if self._metrics_unavailable(metrics):
                metrics = self._extract_perf_metrics_from_pipe(self._pipe)

            raw_text = self._extract_caption_text(result)
            try:
                payload = json.loads(raw_text)
            except json.JSONDecodeError:
                payload = self._fallback_alert_payload(raw_text, event_text)

            normalized = self._normalize_alert_payload(payload, event_text)
            metrics = self._fill_fallback_metrics(metrics, elapsed_ms, raw_text)

        return normalized, metrics

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
