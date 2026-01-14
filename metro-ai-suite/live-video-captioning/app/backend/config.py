import os
from pathlib import Path

APP_PORT = int(os.environ.get("DASHBOARD_PORT", "4173"))
METADATA_FILE = os.environ.get("METADATA_FILE", "/tmp/results.jsonl")
PEER_ID = os.environ.get("WEBRTC_PEER_ID", "genai_pipeline")
SIGNALING_URL = os.environ.get("SIGNALING_URL", "http://localhost:8889")
POLL_INTERVAL = float(os.environ.get("METADATA_POLL_SECONDS", "1"))
AGENT_MODE = os.environ.get("AGENT_MODE", "false").lower() in ("true", "1", "yes")
DEFAULT_RTSP_URL = os.environ.get("DEFAULT_RTSP_URL", "")
ENABLE_DETECTION_PIPELINE = os.environ.get("ENABLE_DETECTION_PIPELINE", "false").lower() in ("true", "1", "yes")

PIPELINE_SERVER_URL = os.environ.get(
    "PIPELINE_SERVER_URL", "http://dlstreamer-pipeline-server:8080"
)
PIPELINE_NAME = os.environ.get("PIPELINE_NAME", "genai_pipeline")

BASE_DIR = Path(__file__).parent.parent
MODELS_DIR = Path(os.environ.get("MODELS_DIR", str(BASE_DIR / "ov_models")))
DETECTION_MODELS_DIR = Path(os.environ.get("DETECTION_MODELS_DIR", str(BASE_DIR / "ov_detection_models")))
UI_DIR = BASE_DIR / "ui"
