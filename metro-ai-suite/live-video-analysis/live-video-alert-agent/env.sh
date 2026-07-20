#!/usr/bin/env bash
# Source this file before running docker compose.
# Usage:
#   source ./env.sh
#   docker compose -f docker/docker-compose.yml up -d --build

export PROJECT_NAME="${PROJECT_NAME:-live-video-alert-agent}"
export REGISTRY="${REGISTRY:-}"
export TAG="${TAG:-test}"

export PORT="${PORT:-9000}"
export VLM_PORT="${VLM_PORT:-8000}"
export LLM_PORT="${LLM_PORT:-8001}"
export ALERT_AGENT_PORT="${ALERT_AGENT_PORT:-8002}"
export METRICS_PORT="${METRICS_PORT:-9090}"
export MQTT_PORT="${MQTT_PORT:-1883}"
export WHIP_SERVER_PORT="${WHIP_SERVER_PORT:-8889}"

export http_proxy="${http_proxy:-}"
export https_proxy="${https_proxy:-}"
export no_proxy="${no_proxy:-localhost,127.0.0.1,ovms-vlm,ovms-llm,alert-agent-service,metrics-manager,mediamtx,coturn,mqtt}"
export NO_PROXY="${NO_PROXY:-$no_proxy}"

export HF_TOKEN="${HF_TOKEN:-}"
export OVMS_SOURCE_MODEL="${OVMS_SOURCE_MODEL:-OpenVINO/Phi-3.5-vision-instruct-int4-ov}"
# export LLM_MODEL="${LLM_MODEL:-OpenVINO/Phi-4-mini-instruct-int4-ov}"

# export OVMS_TARGET_DEVICE="${OVMS_TARGET_DEVICE:-GPU}"
export VLM_TARGET_DEVICE="${VLM_TARGET_DEVICE:-$OVMS_TARGET_DEVICE}"
# export LLM_TARGET_DEVICE="${LLM_TARGET_DEVICE:-$OVMS_TARGET_DEVICE}"
export RENDER_DEVICE_GID="${RENDER_DEVICE_GID:-992}"

export LOG_LEVEL="${LOG_LEVEL:-INFO}"
export MODEL_NAME="${MODEL_NAME:-Phi-3.5-vision}"
export RTSP_URL="${RTSP_URL:-}"
export CAPTURE_FPS="${CAPTURE_FPS:-10}"
export MAX_STREAMS="${MAX_STREAMS:-8}"

export AGENT_MODE="${AGENT_MODE:-true}"
# export LLM_URL="${LLM_URL:-http://ovms-llm:8000/v3}"
# export LLM_TIMEOUT="${LLM_TIMEOUT:-10.0}"
# export ACTION_WORKERS="${ACTION_WORKERS:-2}"
# export ALERT_AGENT_SERVICE_TIMEOUT="${ALERT_AGENT_SERVICE_TIMEOUT:-30.0}"

export WEBHOOK_URL="${WEBHOOK_URL:-}"
export WEBHOOK_SECRET="${WEBHOOK_SECRET:-}"
export MQTT_BROKER="${MQTT_BROKER:-mqtt}"
export MQTT_USERNAME="${MQTT_USERNAME:-}"
export MQTT_PASSWORD="${MQTT_PASSWORD:-}"
export MQTT_BASE_TOPIC="${MQTT_BASE_TOPIC:-alerts/live-video}"

# export MCP_ENABLED="${MCP_ENABLED:-true}"
# export CORS_ORIGINS="${CORS_ORIGINS:-*}"
# export METRICS_POLL_INTERVAL_MS="${METRICS_POLL_INTERVAL_MS:-1000}"

# Critical WebRTC values
export HOST_IP="$(ip route get 1 2>/dev/null | awk '{for (i=1; i<=NF; i++) if ($i == "src") {print $(i+1); exit}}')"
export WEBRTC_SIGNALING_PORT="${WEBRTC_SIGNALING_PORT:-8889}"
export WEBRTC_SIGNALING_URL="${WEBRTC_SIGNALING_URL:-http://${HOST_IP}:${WEBRTC_SIGNALING_PORT}}"
export WEBRTC_AUTO_PUBLISH="${WEBRTC_AUTO_PUBLISH:-true}"
export WEBRTC_RELAY_URL="${WEBRTC_RELAY_URL:-rtsp://mediamtx:8554}"
export MTX_WEBRTCICESERVERS2_0_USERNAME="${MTX_WEBRTCICESERVERS2_0_USERNAME:-localhost}"
export MTX_WEBRTCICESERVERS2_0_PASSWORD="${MTX_WEBRTCICESERVERS2_0_PASSWORD:-localpass}"

echo "[env.sh] Loaded. HOST_IP=${HOST_IP}, WEBRTC_SIGNALING_URL=${WEBRTC_SIGNALING_URL}"