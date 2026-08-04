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
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/app/.venv/bin:$PATH" \
    PORT=9100

RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m appuser

WORKDIR /app

# Virtual environment with installed dependencies
COPY --from=builder /app/.venv /app/.venv

# Application source
COPY --chown=appuser:appuser main.py ./
COPY --chown=appuser:appuser backend/ ./backend/
COPY --chown=appuser:appuser ui/ ./ui/

USER appuser
EXPOSE 9100

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9100"]
