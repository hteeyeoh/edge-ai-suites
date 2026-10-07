# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import os

# Frigate base url
# Get environment variables with defaults (optional)

# Combine safely
FRIGATE_BASE_URL = os.getenv("FRIGATE_BASE_URL")
# VSS Service url
VSS_SUMMARY_URL = os.getenv("VSS_SUMMARY_URL")
VSS_SEARCH_URL = os.getenv("VSS_SEARCH_URL")
no_proxy: str = os.getenv("no_proxy")
MQTT_BROKER = os.getenv("HOST_IP", "mqtt-broker")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1884))
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "frigate/events")
REDIS_HOST = os.getenv("REDIS_HOST", os.getenv("HOST_IP", "redis"))
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
MQTT_USER = os.getenv("MQTT_USER")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")

# Scenescape MQTT Configuration
NVR_SCENESCAPE_ENABLED = os.getenv("NVR_SCENESCAPE", "false").lower() == "true"
SCENESCAPE_MQTT_BROKER = os.getenv("SCENESCAPE_MQTT_BROKER", os.getenv("HOST_IP", "broker"))
SCENESCAPE_MQTT_PORT = int(os.getenv("SCENESCAPE_MQTT_PORT", 1883))
SCENESCAPE_MQTT_TOPIC = os.getenv("SCENESCAPE_MQTT_TOPIC", "scenescape/data/camera/#")
SCENESCAPE_REGION_MQTT_TOPIC = os.getenv(
	"SCENESCAPE_REGION_MQTT_TOPIC", "scenescape/event/region/+/+/+"
)

# Scenescape throttling configuration
SCENESCAPE_THROTTLE_INTERVAL = float(os.getenv("SCENESCAPE_THROTTLE_INTERVAL", 2.0))

# Multi-broker (scenescape mode) configuration
BROKERS_CONFIG_PATH = os.getenv("BROKERS_CONFIG_PATH", "resources/broker-config/brokers.yaml")
MAX_CONCURRENT_EVENTS = int(os.getenv("MAX_CONCURRENT_EVENTS", 50))
BROKER_RECONNECT_DELAY = float(os.getenv("BROKER_RECONNECT_DELAY", 5.0))

# SceneScape API integration settings
SCENESCAPE_API_BASE_URL = os.getenv(
	"SCENESCAPE_API_BASE_URL", "http://metro-vision-ai-app-recipe-web-1"
)
SCENESCAPE_API_VERIFY_SSL = (
	os.getenv("SCENESCAPE_API_VERIFY_SSL", "false").lower() == "true"
)
SCENESCAPE_API_AUTH_PATH = os.getenv("SCENESCAPE_API_AUTH_PATH", "/api/v1/auth")
SCENESCAPE_API_USER = os.getenv(
	"SCENESCAPE_API_USER", os.getenv("SCENESCAPE_USER", "")
)
SCENESCAPE_API_PASSWORD = os.getenv(
	"SCENESCAPE_API_PASSWORD", os.getenv("SCENESCAPE_PASS", "")
)
SCENESCAPE_API_TIMEOUT_SEC = int(os.getenv("SCENESCAPE_API_TIMEOUT_SEC", "4"))
SCENESCAPE_API_RETRY_COUNT = int(os.getenv("SCENESCAPE_API_RETRY_COUNT", "3"))
SCENESCAPE_API_RETRY_DELAY_SEC = float(
	os.getenv("SCENESCAPE_API_RETRY_DELAY_SEC", "0.6")
)

