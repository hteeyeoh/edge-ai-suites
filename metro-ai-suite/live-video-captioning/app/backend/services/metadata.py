from pathlib import Path
from typing import Optional


def read_latest_line(path: Path) -> Optional[str]:
    """Read the latest line from a file."""
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            lines = [line.strip() for line in handle if line.strip()]
    except OSError:
        return None
    if not lines:
        return None
    return lines[-1]
