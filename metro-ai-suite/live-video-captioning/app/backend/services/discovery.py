import json
from pathlib import Path

from ..config import PIPELINE_NAME, PIPELINE_SERVER_URL, ENABLE_DETECTION_PIPELINE
from .http_client import http_json


def discover_models(root: Path) -> list[str]:
    """Discover available models from the models directory."""
    if not root.exists():
        return []
    models: list[str] = []
    for entry in sorted(root.iterdir()):
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            models.append(entry.name)
        else:
            # Allow flat exports placed directly under ov_models
            if entry.suffix in {".xml", ".bin", ".json"}:
                models.append(entry.name)
    return models


def discover_detection_models(root: Path) -> list[str]:
    """Discover available detection models from the detection models directory."""
    if not root.exists():
        return []
    models: list[str] = []
    for entry in sorted(root.iterdir()):
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            # Check if this directory has the expected structure: model_name/public/model_name
            public_dir = entry / "public"
            if public_dir.exists() and public_dir.is_dir():
                # Check if there's a subdirectory with the same name as the parent
                model_subdir = public_dir / entry.name
                if model_subdir.exists() and model_subdir.is_dir():
                    models.append(entry.name)
    return models


def is_detection_pipeline(item: dict) -> bool:
    """Check if the given pipeline item represents a detection pipeline."""
    props = item.get("parameters", {}).get("properties", {})
    if isinstance(props, dict):
        # Any explicit detection fields
        detection_keys = {
            "detection_model_name",
            "detection_threshold",
        }
        # Either keys exist, or any key startswith 'detection_'
        if any(k in props for k in detection_keys):
            return True
        if any(isinstance(k, str) and k.lower().startswith("detection_") for k in props.keys()):
            return True

    return False


def discover_pipelines_remote() -> list[str]:
    """Discover available pipelines from the pipeline server."""
    url = f"{PIPELINE_SERVER_URL.rstrip('/')}/pipelines"
    try:
        raw = http_json("GET", url)
        payload = json.loads(raw)

        # Normalize to a list of items
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            items = payload.get("pipelines") or payload.get("items") or []
        else:
            items = []

        if not isinstance(items, list):
            return [PIPELINE_NAME]

        names: List[str] = []
        for item in items:
            # Determine string name to return
            if isinstance(item, str):
                name = item
            elif isinstance(item, dict) and isinstance(item.get("version"), str):
                name = item["version"]
            else:
                continue

            # Apply filtering only when detection is disabled
            if not ENABLE_DETECTION_PIPELINE and is_detection_pipeline(item):
                continue
            names.append(name)

        return names or [PIPELINE_NAME]

    except Exception:
        return [PIPELINE_NAME]
    return [PIPELINE_NAME]
