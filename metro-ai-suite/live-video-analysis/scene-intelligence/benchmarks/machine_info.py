from __future__ import annotations

import os
import platform
import subprocess


def _run_cmd(command: list[str]) -> str:
    try:
        return subprocess.run(command, check=False, capture_output=True, text=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def get_machine_metadata() -> dict[str, str | int | float | bool]:
    """Collect the hardware and OS metadata used in benchmark CSV output."""
    cpu_model = _run_cmd(["bash", "-lc", "lscpu | grep 'Model name' | head -1 | cut -d ':' -f2- | xargs"])
    if not cpu_model:
        cpu_model = platform.processor() or "unknown"

    cpu_cores = _run_cmd(["bash", "-lc", "nproc --all"])
    try:
        cpu_cores = int(cpu_cores) if cpu_cores else 0
    except ValueError:
        cpu_cores = 0

    ram_total = _run_cmd(["bash", "-lc", "free -m | awk '/^Mem:/ {print $2}'"])
    try:
        ram_total = float(ram_total) / 1024.0 if ram_total else 0.0
    except ValueError:
        ram_total = 0.0

    gpu_model = _run_cmd(["bash", "-lc", "lspci 2>/dev/null | grep -i 'vga\|3d\|display' | head -1 || true"])
    if not gpu_model:
        gpu_model = os.environ.get("GPU_MODEL", "unknown")

    npu_model = os.environ.get("NPU_MODEL", "unknown")
    npu_present = bool(npu_model and npu_model.lower() != "unknown")

    return {
        "machine_name": platform.node() or "unknown",
        "os": platform.system() or "unknown",
        "kernel": platform.release() or "unknown",
        "cpu_model": cpu_model or "unknown",
        "cpu_cores": cpu_cores,
        "cpu_threads": cpu_cores,
        "ram_total_gb": ram_total,
        "gpu_model": gpu_model or "unknown",
        "gpu_memory_gb": 0.0,
        "npu_model": npu_model or "unknown",
        "npu_present": npu_present,
    }
