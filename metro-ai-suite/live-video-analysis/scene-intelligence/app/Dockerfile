# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

# ---------------------------------------------------------------------------
# Stage 1 — dependency installer (uv for fast, reproducible installs)
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    VIRTUAL_ENV=/app/.venv
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY uv.lock pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project

# ---------------------------------------------------------------------------
# Stage 2 — lean runtime image
# ---------------------------------------------------------------------------
# Security note (documented exception): this image intentionally has no
# static `USER appuser` directive. It starts as root so entrypoint.sh can fix
# ownership of the bind-mounted segments volume (which Docker/tmpfs may
# (re)create as root), then immediately drops all privileges to `appuser`
# via `setpriv` before exec'ing the actual app — the running app process is
# never root. See entrypoint.sh for the full rationale.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/app/.venv/bin:$PATH" \
    PORT=9100

# Intel GPU compute-runtime version (see scripts/install_ubuntu_gpu_drivers.sh).
ARG INSTALL_DRIVER_VERSION="26.09.37435"
ENV INSTALL_DRIVER_VERSION=${INSTALL_DRIVER_VERSION}
# Intel NPU user-space stack versions (interpolated into the download URLs).
ARG NPU_DRIVER_VERSION="1.33.0"
ARG NPU_DRIVER_BUILD="v1.33.0.20260529-26625960453-ubuntu2404"
ARG LEVEL_ZERO_VERSION="1.27.0-1~24.04~ppa2"
ARG LEVEL_ZERO_PPA_SNAPSHOT="20260324T100000Z"

RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m appuser

# Install Intel GPU user-mode drivers (OpenVINO VLM_DEVICE=GPU).
COPY scripts/install_ubuntu_gpu_drivers.sh /tmp/install_gpu_drivers.sh
RUN chmod +x /tmp/install_gpu_drivers.sh \
    && /tmp/install_gpu_drivers.sh \
    && rm -f /tmp/install_gpu_drivers.sh && rm -rf /var/lib/apt/lists/* /tmp/*

# Install the Intel NPU user-space stack (OpenVINO VLM_DEVICE=NPU): the NPU
# driver/compiler/firmware plus the oneAPI Level Zero loader (libze1). The
# loader is mandatory — without libze_loader.so.1 the NPU plugin cannot
# enumerate the device. The final test fails the build if it is missing.
RUN mkdir -p /tmp/npu_deps && cd /tmp/npu_deps \
    && apt-get update && apt-get install -y --no-install-recommends libtbb12 \
    && curl -L --retry 5 --retry-delay 5 --retry-all-errors -O https://github.com/intel/linux-npu-driver/releases/download/v${NPU_DRIVER_VERSION}/linux-npu-driver-${NPU_DRIVER_BUILD}.tar.gz \
    && tar -xf linux-npu-driver-${NPU_DRIVER_BUILD}.tar.gz \
    && curl -L --retry 5 --retry-delay 5 --retry-all-errors -O https://snapshot.ppa.launchpadcontent.net/kobuk-team/intel-graphics/ubuntu/${LEVEL_ZERO_PPA_SNAPSHOT}/pool/main/l/level-zero-loader/libze1_${LEVEL_ZERO_VERSION}_amd64.deb \
    && dpkg -i ./*.deb \
    && ldconfig \
    && test -e /usr/lib/x86_64-linux-gnu/libze_loader.so.1 \
    && rm -rf /tmp/npu_deps /var/lib/apt/lists/*

WORKDIR /app

# Virtual environment with installed dependencies
COPY --from=builder /app/.venv /app/.venv

# Application source
COPY --chown=appuser:appuser main.py ./
COPY --chown=appuser:appuser backend/ ./backend/
COPY --chown=appuser:appuser ui/ ./ui/

# Fixes bind-mounted segments-dir ownership at startup, then drops to
# appuser — see entrypoint.sh for why this can't just be `USER appuser`.
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

EXPOSE 9100

ENTRYPOINT ["./entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9100"]
