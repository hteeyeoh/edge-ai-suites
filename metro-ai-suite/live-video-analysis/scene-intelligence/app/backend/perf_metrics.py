# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Shared OpenVINO GenAI perf-metrics extraction (VLM captioner + deep analyzer).

The exact perf-metrics object shape isn't guaranteed across GenAI
versions/devices, so extraction is defensive: try known attribute/key names
first, then fall back to a bounded recursive field walk matching by
normalized name. Used by both ``backend.vlm.VLMEngine`` (single-frame
captioning) and ``backend.deep_analyzer.DeepAnalyzerEngine`` (multi-frame
follow-up) so the two are directly comparable for benchmarking.
"""

from __future__ import annotations

import re
from typing import Any, Optional

_TTFT_NAMES = (
    "ttft",
    "ttft_ms",
    "time_to_first_token",
    "time_to_first_token_ms",
    "first_token_latency",
    "first_token_latency_ms",
)
_TPOT_NAMES = (
    "tpot",
    "tpot_ms",
    "time_per_output_token",
    "time_per_output_token_ms",
    "token_latency",
    "token_latency_ms",
)
_THROUGHPUT_NAMES = (
    "throughput",
    "throughput_tps",
    "tokens_per_second",
    "tok_per_s",
    "tokens_sec",
    "generation_throughput",
    "token_throughput",
)
_NUM_INPUT_TOKENS_NAMES = ("num_input_tokens", "input_tokens", "prompt_tokens")
_NUM_GENERATED_TOKENS_NAMES = (
    "num_generated_tokens",
    "generated_tokens",
    "output_tokens",
    "completion_tokens",
)

_METRIC_FIELD_NAMES = {
    "ttft_ms": _TTFT_NAMES,
    "tpot_ms": _TPOT_NAMES,
    "throughput_tps": _THROUGHPUT_NAMES,
    "num_input_tokens": _NUM_INPUT_TOKENS_NAMES,
    "num_generated_tokens": _NUM_GENERATED_TOKENS_NAMES,
}

_NESTED_METRICS_ATTRS = (
    "metrics",
    "perf_metrics",
    "extended_perf_metrics",
    "performance_metrics",
    "generation_metrics",
)


def empty_metrics() -> dict[str, Optional[float]]:
    return {name: None for name in _METRIC_FIELD_NAMES}


def metrics_unavailable(metrics: dict[str, Optional[float]]) -> bool:
    """True if none of the *timing* fields were found (token counts are best-effort extras)."""
    return metrics.get("ttft_ms") is None and metrics.get("tpot_ms") is None and metrics.get("throughput_tps") is None


def as_float(value: Any) -> Optional[float]:
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


def normalize_token(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def debug_attr_names(obj: Any, limit: int = 80) -> list[str]:
    try:
        names = [n for n in dir(obj) if not str(n).startswith("_")]
    except Exception:  # noqa: BLE001
        return []
    return names[:limit]


def _walk_numeric_fields(
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

    direct_value = as_float(obj)
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
                _walk_numeric_fields(
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
                _walk_numeric_fields(
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
            _walk_numeric_fields(
                value,
                prefix=child_prefix,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items - len(out),
                seen=seen,
            )
        )

    return out


def _extract_metric_from_obj(source: Any, names: tuple[str, ...]) -> Optional[float]:
    if source is None:
        return None

    def resolve_numeric(candidate: Any) -> Optional[float]:
        parsed = as_float(candidate)
        if parsed is not None:
            return parsed

        # Some backends expose statistics objects with mean/avg/value fields.
        if isinstance(candidate, dict):
            for stat_key in ("mean", "avg", "average", "value"):
                if stat_key in candidate:
                    nested = as_float(candidate.get(stat_key))
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
                    nested = as_float(stat_value)
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


def extract_perf_metrics(result: Any) -> dict[str, Optional[float]]:
    """Extract ttft_ms/tpot_ms/throughput_tps/num_input_tokens/num_generated_tokens from a GenAI result."""
    # Try direct result object, then common nested containers.
    candidates = [result]
    for attr_name in _NESTED_METRICS_ATTRS:
        nested = getattr(result, attr_name, None)
        if nested is not None:
            candidates.append(nested)

    values: dict[str, Optional[float]] = {name: None for name in _METRIC_FIELD_NAMES}

    # First pass: direct lookup via common names.
    for candidate in candidates:
        for field, names in _METRIC_FIELD_NAMES.items():
            if values[field] is None:
                values[field] = _extract_metric_from_obj(candidate, names)

    # Second pass: recursive field walk for runtime-specific object shapes.
    if any(v is None for v in values.values()):
        normalized_names = {
            field: {normalize_token(name) for name in names} for field, names in _METRIC_FIELD_NAMES.items()
        }

        for candidate in candidates:
            if not any(v is None for v in values.values()):
                break
            for path, value in _walk_numeric_fields(candidate):
                normalized_path = normalize_token(path)
                for field, tokens in normalized_names.items():
                    if values[field] is None and any(token in normalized_path for token in tokens):
                        values[field] = value
                if not any(v is None for v in values.values()):
                    break

    return values


def extract_perf_metrics_from_pipe(pipe: Any) -> dict[str, Optional[float]]:
    if pipe is None:
        return empty_metrics()

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
        return empty_metrics()

    return extract_perf_metrics(metrics_obj)


def estimate_token_count(text: str) -> int:
    cleaned = str(text or "").strip()
    if not cleaned:
        return 0
    # Lightweight approximation for fallback metrics.
    return max(1, len(cleaned.split()))


def fill_fallback_metrics(
    metrics: dict[str, Optional[float]],
    elapsed_ms: float,
    generated_text: str,
) -> dict[str, Optional[float]]:
    """Fill in ttft_ms/tpot_ms/throughput_tps/num_generated_tokens when the runtime didn't report them."""
    estimated_tokens = estimate_token_count(generated_text)
    safe_elapsed_ms = max(0.0, float(elapsed_ms))

    if metrics.get("num_generated_tokens") is None:
        metrics["num_generated_tokens"] = float(estimated_tokens)

    token_count = metrics.get("num_generated_tokens") or estimated_tokens

    if metrics.get("ttft_ms") is None:
        # Conservative fallback: TTFT approximated with full generation latency.
        metrics["ttft_ms"] = safe_elapsed_ms

    inferred_tpot = None
    if token_count and token_count > 0:
        inferred_tpot = safe_elapsed_ms / token_count

    if metrics.get("tpot_ms") is None:
        metrics["tpot_ms"] = inferred_tpot

    if metrics.get("throughput_tps") is None:
        tpot_value = metrics.get("tpot_ms")
        if tpot_value is not None and tpot_value > 0:
            metrics["throughput_tps"] = 1000.0 / tpot_value

    return metrics
