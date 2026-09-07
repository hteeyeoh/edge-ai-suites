# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

FROM python:3.12-slim AS base

# Install GPU drivers.
ARG INSTALL_DRIVER_VERSION="26.18.38308"
COPY scripts/install_ubuntu_gpu_drivers.sh /tmp/drivers/install_gpu_drivers.sh
RUN chmod +x /tmp/drivers/install_gpu_drivers.sh

RUN echo "Installing GPU drivers version $INSTALL_DRIVER_VERSION" \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl libxml2 \
    && python -m pip install --upgrade --no-cache-dir pip==26.1.2 \
    && /tmp/drivers/install_gpu_drivers.sh \
    && apt-get clean \
    && rm -rf /tmp/drivers /var/lib/apt/lists/* /tmp/*

# Install the Intel NPU user-space stack: the NPU
# driver/compiler/firmware plus the oneAPI Level Zero loader (libze1). The
# loader is mandatory — without libze_loader.so.1 the NPU plugin cannot
# enumerate the device. The final test fails the build if it is missing.
ARG NPU_DRIVER_VERSION="1.35.0"
ARG NPU_DRIVER_BUILD="v1.35.0.20260722-29947505341-ubuntu2404"
ARG LEVEL_ZERO_VERSION="1.28.2-1~24.04~ppa1"
ARG LEVEL_ZERO_PPA_SNAPSHOT="20260606T100000Z"

RUN mkdir -p /tmp/npu_deps && cd /tmp/npu_deps \
    && apt-get update && apt-get install -y --no-install-recommends curl ca-certificates libtbb12 \
    && curl -L --retry 5 --retry-delay 5 --retry-all-errors -O https://github.com/intel/linux-npu-driver/releases/download/v${NPU_DRIVER_VERSION}/linux-npu-driver-${NPU_DRIVER_BUILD}.tar.gz \
    && tar -xf linux-npu-driver-${NPU_DRIVER_BUILD}.tar.gz \
    && curl -L --retry 5 --retry-delay 5 --retry-all-errors -O https://snapshot.ppa.launchpadcontent.net/kobuk-team/intel-graphics/ubuntu/${LEVEL_ZERO_PPA_SNAPSHOT}/pool/main/l/level-zero-loader/libze1_${LEVEL_ZERO_VERSION}_amd64.deb \
    && dpkg -i ./*.deb \
    && ldconfig \
    && test -e /usr/lib/x86_64-linux-gnu/libze_loader.so.1 \
    && apt-get purge -y curl && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /tmp/npu_deps /var/lib/apt/lists/*

# Build stage.
FROM base AS builder

# Install system dependencies.
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv.
RUN pip install --no-cache-dir uv==0.11.10

WORKDIR /app

# Copy dependency manifests and install dependencies into a dedicated venv.
COPY pyproject.toml uv.lock ./
RUN uv venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV UV_PROJECT_ENVIRONMENT="/opt/venv"
RUN uv sync --frozen --no-dev --no-install-project

# Production stage.
FROM base AS production

RUN apt-get update && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user used by the app process after entrypoint drops privileges.
RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid appuser --shell /bin/bash --create-home appuser

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/opt/venv/bin:$PATH" \
    PORT=9100

# Virtual environment with installed dependencies.
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Application source.
COPY --chown=appuser:appuser main.py ./
COPY --chown=appuser:appuser backend/ ./backend/
COPY --chown=appuser:appuser ui/ ./ui/

# Fixes bind-mounted segments-dir ownership at startup, then drops to
# appuser — see entrypoint.sh for why this can't just be USER appuser.
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

EXPOSE 9100

ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9100"]
