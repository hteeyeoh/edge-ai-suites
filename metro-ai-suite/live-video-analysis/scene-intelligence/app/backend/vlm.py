# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""OVMS-backed vision-language captioning engine.

This module sends sampled frames to OVMS via the OpenAI-compatible
``/v3/chat/completions`` endpoint. Calls are serialized with a lock because
multiple stream workers share one client instance.
"""

from __future__ import annotations

import logging
import base64
import json
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Optional

import numpy as np

from backend.config import settings

logger = logging.getLogger(__name__)


class VLMEngine:
    """Thread-safe wrapper around OVMS chat/completions."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        base_url = settings.VLM_OVMS_BASE_URL.rstrip("/")
        chat_path = settings.VLM_OVMS_CHAT_PATH
        if not str(chat_path).startswith("/"):
            chat_path = f"/{chat_path}"
        self._endpoint = f"{base_url}{chat_path}"
        self._model_name = settings.VLM_OVMS_MODEL
        self._timeout_s = max(1.0, float(settings.VLM_OVMS_TIMEOUT))
        self._load()
        self._metrics_debug_logged = False

    def _load(self) -> None:
        logger.info(
            "Initializing OVMS VLM client | endpoint=%s | model=%s",
            self._endpoint,
            self._model_name,
        )

    @staticmethod
    def _frame_to_data_url(rgb_frame: np.ndarray) -> str:
        """Encode RGB uint8 frame into a PPM data URL (no extra deps required)."""
        if rgb_frame is None:
            raise ValueError("rgb_frame is required")
        if not hasattr(rgb_frame, "shape") or len(rgb_frame.shape) != 3:
            raise ValueError("rgb_frame must be HxWx3")
        if rgb_frame.shape[2] != 3:
            raise ValueError("rgb_frame must have 3 channels (RGB)")

        if rgb_frame.dtype != np.uint8:
            rgb_frame = np.clip(rgb_frame, 0, 255).astype(np.uint8)

        h, w, _ = rgb_frame.shape
        contiguous = np.ascontiguousarray(rgb_frame)
        header = f"P6\n{w} {h}\n255\n".encode("ascii")
        ppm_bytes = header + contiguous.tobytes(order="C")
        encoded = base64.b64encode(ppm_bytes).decode("ascii")
        return f"data:image/x-portable-pixmap;base64,{encoded}"

    @staticmethod
    def _extract_completion_text(payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            choice0 = choices[0] if isinstance(choices[0], dict) else {}
            message = choice0.get("message") if isinstance(choice0, dict) else None
            content = message.get("content") if isinstance(message, dict) else None

            if isinstance(content, str):
                return content.strip()

            if isinstance(content, list):
                text_parts: list[str] = []
                for item in content:
                    if isinstance(item, str):
                        text_parts.append(item)
                        continue
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") == "text" and isinstance(item.get("text"), str):
                        text_parts.append(item["text"])
                joined = "\n".join(p.strip() for p in text_parts if p and p.strip())
                if joined:
                    return joined

        # Fallbacks for variant schemas.
        for key in ("output_text", "text", "response"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()

        return ""

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

    def _send_chat_completion(self, rgb_frame: np.ndarray, prompt_text: str) -> dict[str, Any]:
        image_data_url = self._frame_to_data_url(rgb_frame)
        payload = {
            "model": self._model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                    ],
                }
            ],
            "max_tokens": settings.VLM_MAX_TOKENS,
        }

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            error_text = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"OVMS HTTP {exc.code} calling {self._endpoint}: {error_text[:600]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OVMS request failed for {self._endpoint}: {exc}") from exc

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"OVMS returned non-JSON response: {raw[:600]}") from exc

        if not isinstance(parsed, dict):
            raise RuntimeError("OVMS response payload must be a JSON object")
        return parsed

    def caption_with_metrics(
        self,
        rgb_frame: np.ndarray,
        prompt: Optional[str] = None,
    ) -> tuple[str, dict[str, Optional[float]]]:
        """Return caption text and optional latency/throughput metrics."""
        prompt_text = (prompt or "").strip()
        if not prompt_text:
            raise ValueError("prompt is required")

        with self._lock:
            t0 = time.perf_counter()
            result = self._send_chat_completion(rgb_frame, prompt_text)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            metrics = self._extract_perf_metrics(result)
            caption_text = self._extract_completion_text(result)
            if not caption_text:
                raise RuntimeError("OVMS response did not include completion text")

            if not self._metrics_debug_logged and self._metrics_unavailable(metrics):
                self._metrics_debug_logged = True
                logger.warning("VLM metrics unavailable from OVMS response; using fallback estimates")

            usage = result.get("usage") if isinstance(result, dict) else None
            completion_tokens = None
            if isinstance(usage, dict):
                completion_tokens_raw = usage.get("completion_tokens")
                try:
                    completion_tokens = int(completion_tokens_raw)
                except (TypeError, ValueError):
                    completion_tokens = None

            if metrics.get("tpot_ms") is None and completion_tokens and completion_tokens > 0:
                metrics["tpot_ms"] = elapsed_ms / completion_tokens
            if metrics.get("throughput_tps") is None and metrics.get("tpot_ms"):
                tpot = metrics["tpot_ms"]
                if tpot and tpot > 0:
                    metrics["throughput_tps"] = 1000.0 / tpot

            metrics = self._fill_fallback_metrics(metrics, elapsed_ms, caption_text)

        return caption_text, metrics

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
