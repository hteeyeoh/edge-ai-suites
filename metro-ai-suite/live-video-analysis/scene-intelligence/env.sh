#!/usr/bin/env bash
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#
# Source this file before running docker compose so the browser can reach
# MediaMTX/coturn on the host's LAN address for WebRTC.
#
#   source ./env.sh
#   docker compose up -d --build

export PROJECT_NAME="${PROJECT_NAME:-scene-intelligence}"
export TAG="${TAG:-latest}"

export PORT="${PORT:-9100}"
export RTSP_SERVER_PORT="${RTSP_SERVER_PORT:-8554}"
export WHEP_SERVER_PORT="${WHEP_SERVER_PORT:-8889}"
export METRICS_SERVICE_PORT="${METRICS_SERVICE_PORT:-9090}"

export LOG_LEVEL="${LOG_LEVEL:-INFO}"

# ---- video source ----
export RTSP_URL="${RTSP_URL:-}"
export RTSP_TRANSPORT="${RTSP_TRANSPORT:-tcp}"

# ---- proxy passthrough ----
export http_proxy="${http_proxy:-}"
export https_proxy="${https_proxy:-}"
export no_proxy="${no_proxy:-localhost,127.0.0.1,mediamtx,coturn,scene-intelligence,metrics-manager}"
export NO_PROXY="${NO_PROXY:-$no_proxy}"

# ---- WebRTC ----
# Host LAN IP so browsers on other machines can reach MediaMTX/coturn.
export HOST_IP="${HOST_IP:-$(ip route get 1 2>/dev/null | awk '{for (i=1; i<=NF; i++) if ($i == "src") {print $(i+1); exit}}')}"
export WEBRTC_SIGNALING_PORT="${WEBRTC_SIGNALING_PORT:-8889}"
export WEBRTC_SIGNALING_URL="${WEBRTC_SIGNALING_URL:-http://${HOST_IP}:${WEBRTC_SIGNALING_PORT}}"
export WEBRTC_RELAY_URL="${WEBRTC_RELAY_URL:-rtsp://mediamtx:8554}"
export WEBRTC_AUTO_PUBLISH="${WEBRTC_AUTO_PUBLISH:-true}"
export TURN_USERNAME="${TURN_USERNAME:-scene}"
export TURN_PASSWORD="${TURN_PASSWORD:-scenepass}"

echo "[env.sh] HOST_IP=${HOST_IP} WEBRTC_SIGNALING_URL=${WEBRTC_SIGNALING_URL}"
