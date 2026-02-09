# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import os
from pathlib import Path

APP_PORT = int(os.environ.get("DASHBOARD_PORT", "4173"))
PEER_ID = os.environ.get("WEBRTC_PEER_ID", "genai_pipeline")
SIGNALING_URL = os.environ.get("SIGNALING_URL", "http://localhost:8889")
WEBRTC_BITRATE = int(os.environ.get("WEBRTC_BITRATE", "2048"))
AGENT_MODE = os.environ.get("AGENT_MODE", "false").lower() in ("true", "1", "yes")
DEFAULT_RTSP_URL = os.environ.get("DEFAULT_RTSP_URL", "")
ENABLE_DETECTION_PIPELINE = os.environ.get(
    "ENABLE_DETECTION_PIPELINE", "false"
).lower() in ("true", "1", "yes")

# Metrics Service Configuration
METRICS_SERVICE_PORT = os.environ.get("METRICS_SERVICE_PORT", "9090")

# MQTT Configuration
MQTT_BROKER_HOST = os.environ.get("MQTT_BROKER_HOST", "mqtt-broker")
MQTT_BROKER_PORT = int(os.environ.get("MQTT_BROKER_PORT", "1883"))
MQTT_TOPIC_PREFIX = os.environ.get("MQTT_TOPIC_PREFIX", "live-video-captioning")

PIPELINE_SERVER_URL = os.environ.get(
    "PIPELINE_SERVER_URL", "http://dlstreamer-pipeline-server:8080"
)
PIPELINE_NAME = os.environ.get("PIPELINE_NAME", "genai_pipeline")

BASE_DIR = Path(__file__).parent.parent
MODELS_DIR = Path(os.environ.get("MODELS_DIR", str(BASE_DIR / "ov_models")))
DETECTION_MODELS_DIR = Path(
    os.environ.get("DETECTION_MODELS_DIR", str(BASE_DIR / "ov_detection_models"))
)
UI_DIR = BASE_DIR / "ui"

# Embedding / VDMS Configuration
VDMS_HOST = os.getenv("VDMS_HOST", "localhost")
VDMS_PORT = int(os.getenv("VDMS_PORT", "5555"))
EMBEDDING_HOST = os.getenv("EMBEDDING_HOST", "localhost")
EMBEDDING_PORT = int(os.getenv("EMBEDDING_PORT", "5000"))
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "")
ENABLE_EMBEDDING = os.environ.get("ENABLE_EMBEDDING", "false").lower() in ("true", "1", "yes")
EMBEDDING_LENGTH: int = 0

# Proxy settings
NO_PROXY_ENV = os.environ.get("no_proxy", "")  # Comma-separated domains for no-proxy
HTTP_PROXY = os.environ.get("http_proxy", "")
HTTPS_PROXY = os.environ.get("https_proxy", "")