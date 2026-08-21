from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


@dataclass
class BenchmarkConfig:
    """Configuration used by the dev-only benchmark harness."""

    run_id: str = "benchmark-run"
    duration_seconds: int = 180
    warmup_seconds: int = 30
    sample_interval_seconds: int = 5
    max_cpu_util_pct: float = 85.0
    max_ram_util_pct: float = 85.0
    max_gpu_util_pct: float = 80.0
    max_npu_util_pct: float = 80.0
    max_ttft_ms: float = 2500.0
    max_tpot_ms: float = 250.0
    api_base_url: str = ""
    output_dir: Path = Path("results")
    raw_csv_name: str = "raw_benchmark.csv"

    def output_path(self) -> Path:
        root = Path(self.output_dir)
        safe_run_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", self.run_id).strip("._") or "benchmark-run"
        raw_stem = Path(self.raw_csv_name).stem
        raw = root / "raw" / f"{raw_stem}_{safe_run_id}.csv"
        raw.parent.mkdir(parents=True, exist_ok=True)
        return raw
