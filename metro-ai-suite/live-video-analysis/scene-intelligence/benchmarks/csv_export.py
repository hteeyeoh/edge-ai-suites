from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

RAW_FIELDNAMES = [
    "run_id",
    "timestamp_utc",
    "metric_source",
    "stream_id",
    "alert_vlm_model",
    "alert_vlm_device",
    "alert_vlm_ttft_ms",
    "alert_vlm_tpot_ms",
    "alert_vlm_throughput_tps",
    "alert_vlm_total_tokens_generated",
    "deep_analyzer_model",
    "deep_analyzer_device",
    "deep_segments_submitted",
    "deep_segments_queued",
    "deep_segments_in_flight",
    "deep_segments_active",
    "deep_segments_completed",
    "deep_segments_persisted",
    "deep_segments_failed",
    "deep_segments_max_in_flight",
    "deep_segments_processed_10s",
    "deep_segments_per_second_10s",
    "deep_ttft_ms",
    "deep_tpot_ms",
    "deep_throughput_tps",
    "deep_total_duration_ms",
    "deep_frames_sampled",
    "deep_total_tokens_generated",
    "cpu_util_pct",
    "ram_util_pct",
    "gpu_util_pct",
    "npu_util_pct",
]


def write_raw_csv(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=RAW_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in RAW_FIELDNAMES})
