#!/usr/bin/env bash

# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_EXAMPLE="${ROOT_DIR}/.env.example"
ENV_FILE="${ROOT_DIR}/.env"
FORCE=false

usage() {
  cat <<EOF
Usage: bash scripts/setup_env.sh [--force]

Creates ${ENV_FILE} from .env.example and fills host-specific values such as
HOST_IP, WEBRTC_SIGNALING_URL, HOST_UID, HOST_GID, and RENDER_GROUP_ID.

Options:
  --force     Overwrite an existing .env file.
  -h, --help  Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force)
      FORCE=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ ! -f "${ENV_EXAMPLE}" ]]; then
  echo "ERROR: Missing template: ${ENV_EXAMPLE}" >&2
  exit 1
fi

if [[ -f "${ENV_FILE}" && "${FORCE}" != "true" ]]; then
  echo ".env already exists. Leaving it unchanged."
  echo "Use 'bash scripts/setup_env.sh --force' to regenerate it."
  exit 0
fi

HOST_IP="$(ip route get 1 2>/dev/null | awk '{for (i=1; i<=NF; i++) if ($i == "src") {print $(i+1); exit}}')"
HOST_IP="${HOST_IP:-127.0.0.1}"

WEBRTC_SIGNALING_PORT="$(awk -F= '$1 == "WEBRTC_SIGNALING_PORT" {print $2; exit}' "${ENV_EXAMPLE}")"
WEBRTC_SIGNALING_PORT="${WEBRTC_SIGNALING_PORT:-8889}"
WEBRTC_SIGNALING_URL="http://${HOST_IP}:${WEBRTC_SIGNALING_PORT}"

HOST_UID="$(id -u)"
HOST_GID="$(id -g)"

RENDER_GROUP_ID="$(getent group render 2>/dev/null | cut -d: -f3)"
RENDER_GROUP_ID="${RENDER_GROUP_ID:-992}"

tmp_file="$(mktemp)"
trap 'rm -f "${tmp_file}"' EXIT

while IFS= read -r line || [[ -n "$line" ]]; do
  case "$line" in
    HOST_IP=*)
      printf 'HOST_IP=%s\n' "${HOST_IP}" >> "${tmp_file}"
      ;;
    WEBRTC_SIGNALING_URL=*)
      printf 'WEBRTC_SIGNALING_URL=%s\n' "${WEBRTC_SIGNALING_URL}" >> "${tmp_file}"
      ;;
    HOST_UID=*)
      printf 'HOST_UID=%s\n' "${HOST_UID}" >> "${tmp_file}"
      ;;
    HOST_GID=*)
      printf 'HOST_GID=%s\n' "${HOST_GID}" >> "${tmp_file}"
      ;;
    RENDER_GROUP_ID=*)
      printf 'RENDER_GROUP_ID=%s\n' "${RENDER_GROUP_ID}" >> "${tmp_file}"
      ;;
    *)
      printf '%s\n' "$line" >> "${tmp_file}"
      ;;
  esac
done < "${ENV_EXAMPLE}"

mv "${tmp_file}" "${ENV_FILE}"
trap - EXIT

echo "Created ${ENV_FILE}"
echo "HOST_IP=${HOST_IP}"
echo "WEBRTC_SIGNALING_URL=${WEBRTC_SIGNALING_URL}"
echo "HOST_UID=${HOST_UID}"
echo "HOST_GID=${HOST_GID}"
echo "RENDER_GROUP_ID=${RENDER_GROUP_ID}"
echo "UI URL: http://${HOST_IP}:9100"
