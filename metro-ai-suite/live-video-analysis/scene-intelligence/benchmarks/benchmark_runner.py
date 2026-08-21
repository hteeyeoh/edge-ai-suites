from __future__ import annotations

import ipaddress
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request
from urllib.parse import urlparse

try:
    from .config import BenchmarkConfig
    from .csv_export import write_raw_csv
except ImportError:  # pragma: no cover - direct script fallback
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from benchmarks.config import BenchmarkConfig
    from benchmarks.csv_export import write_raw_csv


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _url_opener(url: str):
    """Use a direct connection for app hosts on private or loopback networks."""
    hostname = urlparse(url).hostname or ""
    is_local_name = hostname.lower() in {"localhost", "localhost.localdomain"}
    try:
        is_private_address = ipaddress.ip_address(hostname).is_private
    except ValueError:
        is_private_address = False
    if is_local_name or is_private_address:
        return request.build_opener(request.ProxyHandler({}))
    return request


def _fetch_json(url: str) -> dict | list | None:
    try:
        with _url_opener(url).open(url, timeout=10) as response:
            payload = response.read().decode("utf-8")
            return json.loads(payload)
    except (error.URLError, ValueError, TimeoutError):
        return None


def _fetch_runtime_config(api_base_url: str) -> dict:
    """Read model and device settings exported by the running app."""
    base = api_base_url.rstrip("/")
    try:
        with _url_opener(f"{base}/runtime-config.js").open(
            f"{base}/runtime-config.js", timeout=10
        ) as response:
            body = response.read().decode("utf-8")
        prefix = "window.RUNTIME_CONFIG = "
        if not body.startswith(prefix):
            return {}
        payload = body[len(prefix):].rstrip().rstrip(";").strip()
        config = json.loads(payload)
        return config if isinstance(config, dict) else {}
    except (error.URLError, ValueError, TimeoutError):
        return {}


def _runtime_model_config(runtime_config: dict) -> tuple[str, str, str, str]:
    """Resolve current VLM and deep-analyzer model/device labels."""
    vlm_model = str(
        runtime_config.get("alertVlmModel")
        or os.environ.get("ALERT_VLM_MODEL")
        or "unknown"
    )
    vlm_device = str(
        runtime_config.get("alertVlmDevice")
        or os.environ.get("ALERT_VLM_DEVICE")
        or "unknown"
    )
    deep_model = str(
        runtime_config.get("deepAnalyzerModel")
        or os.environ.get("DEEP_ANALYZER_MODEL", "unknown")
    )
    deep_device = str(
        runtime_config.get("deepAnalyzerDevice")
        or os.environ.get("DEEP_ANALYZER_DEVICE", "unknown")
    )
    return vlm_model, vlm_device, deep_model, deep_device


def _infer_metrics_service_url(api_base_url: str) -> str:
    if not api_base_url:
        return ""
    base = api_base_url.rstrip("/")
    parsed = urlparse(base)
    host = parsed.hostname or "localhost"
    port = parsed.port or 9100
    default_port = 9090
    scheme = parsed.scheme or "http"
    if port == default_port:
        return f"{scheme}://{host}:{default_port}/metrics/stream"
    return f"{scheme}://{host}:{default_port}/metrics/stream"


def _aggregate_live_resource_metrics(payload: dict | list | None) -> dict[str, float]:
    metrics = payload.get("metrics", []) if isinstance(payload, dict) else []
    if not isinstance(metrics, list):
        return {
            "cpu": 0.0,
            "ram": 0.0,
            "gpu": 0.0,
            "npu": 0.0,
        }

    cpu_values: list[float] = []
    ram_values: list[float] = []
    gpu_values: list[float] = []
    npu_values: list[float] = []

    for metric in metrics:
        if not isinstance(metric, dict):
            continue
        name = str(metric.get("name", ""))
        labels = metric.get("labels", {}) or {}
        value = _safe_float(metric.get("value"))
        if name == "cpu_usage_user" and (labels.get("cpu") in (None, "cpu-total", "cpu-total")):
            cpu_values.append(value)
        elif name == "mem_used_percent":
            ram_values.append(value)
        elif name == "gpu_engine_usage_usage":
            if labels.get("engine") is not None:
                gpu_values.append(value)
        elif name == "npu_utilization":
            npu_values.append(value)

    return {
        "cpu": max(cpu_values) if cpu_values else 0.0,
        "ram": max(ram_values) if ram_values else 0.0,
        "gpu": max(gpu_values) if gpu_values else 0.0,
        "npu": max(npu_values) if npu_values else 0.0,
    }


def _pull_runtime_metrics(api_base_url: str) -> dict[str, float]:
    if not api_base_url:
        return {"cpu": 0.0, "ram": 0.0, "gpu": 0.0, "npu": 0.0}

    metrics_url = _infer_metrics_service_url(api_base_url)
    if not metrics_url:
        return {"cpu": 0.0, "ram": 0.0, "gpu": 0.0, "npu": 0.0}

    try:
        with _url_opener(metrics_url).open(metrics_url, timeout=8) as response:
            buffer = ""
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                chunk = response.read(4096)
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", errors="ignore")
                if "data:" in buffer:
                    break
            if not buffer:
                return {"cpu": 0.0, "ram": 0.0, "gpu": 0.0, "npu": 0.0}

            payload_text = ""
            for line in buffer.splitlines():
                if line.startswith("data:"):
                    payload_text = line[5:].strip()
                    break
            if not payload_text:
                return {"cpu": 0.0, "ram": 0.0, "gpu": 0.0, "npu": 0.0}
            payload = json.loads(payload_text)
            return _aggregate_live_resource_metrics(payload)
    except Exception:  # noqa: BLE001
        return {"cpu": 0.0, "ram": 0.0, "gpu": 0.0, "npu": 0.0}


def _runtime_rows_from_api(api_base_url: str, config: BenchmarkConfig) -> list[dict]:
    """Collect live per-stream VLM metrics from the running app via /streams."""
    if not api_base_url:
        return []
    base = api_base_url.rstrip("/")
    payload = _fetch_json(f"{base}/streams")
    if not isinstance(payload, dict):
        return []
    streams = payload.get("streams", [])
    if not isinstance(streams, list):
        return []

    live_resources = _pull_runtime_metrics(api_base_url)
    runtime_config = _fetch_runtime_config(api_base_url)
    vlm_model, vlm_device, deep_model, deep_device = _runtime_model_config(runtime_config)
    rows: list[dict] = []
    for stream in streams:
        stream_id = str(stream.get("stream_id", "unknown-stream"))
        deep_metrics = _fetch_json(f"{base}/streams/{stream_id}/deep-metrics")
        if not isinstance(deep_metrics, dict):
            deep_metrics = {}
        common_row = {
            "run_id": config.run_id,
            "timestamp_utc": _utc_now(),
            "stream_id": stream_id,
            "deep_segments_completed": int(deep_metrics.get("deep_segments_completed", 0) or 0),
            "deep_segments_persisted": int(deep_metrics.get("deep_segments_persisted", 0) or 0),
            "deep_segments_submitted": int(deep_metrics.get("deep_segments_submitted", 0) or 0),
            "deep_segments_queued": int(deep_metrics.get("deep_segments_queued", 0) or 0),
            "deep_segments_in_flight": int(deep_metrics.get("deep_segments_in_flight", 0) or 0),
            "deep_segments_active": int(deep_metrics.get("deep_segments_active", 0) or 0),
            "deep_segments_failed": int(deep_metrics.get("deep_segments_failed", 0) or 0),
            "deep_segments_max_in_flight": int(deep_metrics.get("deep_segments_max_in_flight", 0) or 0),
            "deep_segments_processed_10s": "",
            "deep_segments_per_second_10s": "",
            "cpu_util_pct": live_resources["cpu"],
            "ram_util_pct": live_resources["ram"],
            "gpu_util_pct": live_resources["gpu"],
            "npu_util_pct": live_resources["npu"],
        }
        rows.append(
            {
                **common_row,
                "metric_source": "alert_vlm",
                "alert_vlm_model": vlm_model,
                "alert_vlm_device": vlm_device,
                "alert_vlm_ttft_ms": _safe_float(stream.get("ttft_ms")),
                "alert_vlm_tpot_ms": _safe_float(stream.get("tpot_ms")),
                "alert_vlm_throughput_tps": _safe_float(stream.get("throughput_tps")),
                "alert_vlm_total_tokens_generated": _safe_float(
                    stream.get("total_tokens_generated")
                ),
            }
        )
        rows.append(
            {
                **common_row,
                "metric_source": "deep_analyzer",
                "deep_analyzer_model": str(deep_metrics.get("deep_analyzer_model") or deep_model),
                "deep_analyzer_device": str(deep_metrics.get("deep_analyzer_device") or deep_device),
                "deep_ttft_ms": _safe_float(deep_metrics.get("deep_ttft_ms")),
                "deep_tpot_ms": _safe_float(deep_metrics.get("deep_tpot_ms")),
                "deep_throughput_tps": _safe_float(deep_metrics.get("deep_throughput_tps")),
                "deep_total_duration_ms": _safe_float(deep_metrics.get("deep_total_duration_ms")),
                "deep_frames_sampled": _safe_float(deep_metrics.get("deep_frames_sampled")),
                "deep_total_tokens_generated": _safe_float(
                    deep_metrics.get("deep_total_tokens_generated")
                ),
            }
        )
    return rows


def _add_deep_window_metrics(
    rows: list[dict],
    baselines: dict[str, tuple[float, int]],
    now: float,
    window_seconds: int = 10,
) -> None:
    """Add completed-segment deltas for non-overlapping 10-second windows."""
    for row in rows:
        if row.get("metric_source") != "deep_analyzer":
            continue
        stream_id = str(row["stream_id"])
        completed = int(row.get("deep_segments_completed", 0) or 0)
        baseline = baselines.get(stream_id)
        if baseline is None:
            baselines[stream_id] = (now, completed)
            continue
        baseline_time, baseline_completed = baseline
        elapsed = now - baseline_time
        if elapsed < window_seconds:
            continue
        processed = max(0, completed - baseline_completed)
        row["deep_segments_processed_10s"] = processed
        row["deep_segments_per_second_10s"] = processed / elapsed
        baselines[stream_id] = (now, completed)


def run_benchmark_suite(config: BenchmarkConfig | None = None) -> tuple[list[dict], Path]:
    cfg = config or BenchmarkConfig()
    if not cfg.api_base_url:
        raise RuntimeError("A live app URL is required; pass --api-base-url http://localhost:9100")

    if cfg.warmup_seconds > 0:
        time.sleep(cfg.warmup_seconds)

    samples: list[list[dict]] = []
    deep_window_baselines: dict[str, tuple[float, int]] = {}
    deadline = time.monotonic() + max(0, cfg.duration_seconds)
    while True:
        snapshot = _runtime_rows_from_api(cfg.api_base_url, cfg)
        if snapshot:
            _add_deep_window_metrics(snapshot, deep_window_baselines, time.monotonic())
            samples.append(snapshot)
        if time.monotonic() >= deadline:
            break
        time.sleep(max(1, cfg.sample_interval_seconds))

    if not samples:
        raise RuntimeError(
            f"No live stream telemetry was received from {cfg.api_base_url.rstrip('/')}/streams. "
            "Verify that the app is running and has at least one active stream."
        )

    raw_rows = [row for snapshot in samples for row in snapshot]
    raw_path = cfg.output_path()
    write_raw_csv(raw_path, raw_rows)
    return raw_rows, raw_path


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Dev-only benchmark harness for Scene Intelligence.")
    parser.add_argument(
        "--api-base-url",
        required=True,
        help="Base URL of the running app (for example http://localhost:9100).",
    )
    parser.add_argument("--run-id", default="benchmark-run", help="Benchmark run ID.")
    parser.add_argument("--duration-seconds", type=int, default=180, help="Measurement duration recorded in each row.")
    parser.add_argument("--warmup-seconds", type=int, default=30, help="Warmup duration recorded for the benchmark plan.")
    parser.add_argument("--sample-interval-seconds", type=int, default=5, help="Seconds between live telemetry samples.")
    args = parser.parse_args()

    config = BenchmarkConfig(
        run_id=args.run_id,
        api_base_url=args.api_base_url,
        duration_seconds=args.duration_seconds,
        warmup_seconds=args.warmup_seconds,
        sample_interval_seconds=args.sample_interval_seconds,
    )
    try:
        raw_rows, raw_path = run_benchmark_suite(config)
    except RuntimeError as exc:
        parser.error(str(exc))
    print(f"Benchmark raw CSV: {raw_path}")
    print(f"Completed {len(raw_rows)} benchmark rows")


if __name__ == "__main__":
    main()
