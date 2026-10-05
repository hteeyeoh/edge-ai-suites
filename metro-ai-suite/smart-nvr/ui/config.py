# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
import os
import logging
from dotenv import load_dotenv

load_dotenv()

API_BASE_URL = os.getenv("API_BASE_URL")
SCENESCAPE_API_BASE_URL = os.getenv(
    "SCENESCAPE_API_BASE_URL", "http://metro-vision-ai-app-recipe-web-1"
)
# Fixed for this deployment profile.
SCENESCAPE_API_VERIFY_SSL = False
SCENESCAPE_API_AUTH_PATH = "/api/v1/auth"
SCENESCAPE_API_USER = os.getenv("SCENESCAPE_API_USER", os.getenv("SCENESCAPE_USER", ""))
SCENESCAPE_API_PASSWORD = os.getenv(
    "SCENESCAPE_API_PASSWORD", os.getenv("SCENESCAPE_PASS", "")
)
EVENT_POLL_INTERVAL = int(os.getenv("EVENT_POLL_INTERVAL", "10"))

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("vms_event_router_ui.log")],
)
logger = logging.getLogger("VMS_UI")
