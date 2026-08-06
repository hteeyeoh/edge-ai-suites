# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Scene Intelligence — FastAPI application.

Step 1 scope: ingest an RTSP source with PyAV, remux it into MediaMTX, and
render it in the browser over WebRTC (WHEP).
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from backend.config import build_alert_prompt, settings, setup_logging
from backend.registry import StreamRegistry

setup_logging()
logger = logging.getLogger(__name__)

_startup_time = time.monotonic()
registry = StreamRegistry()

_UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Scene Intelligence | port=%s", settings.PORT)
    logger.info("No startup stream auto-registration — add streams via UI or POST /streams")
    try:
        yield
    finally:
        logger.info("Shutting down — stopping streams ...")
        registry.stop_all()


app = FastAPI(
    title="Scene Intelligence",
    description="RTSP ingestion and WebRTC rendering pipeline powered by PyAV.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_static_dir = os.path.join(_UI_DIR, "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------


@app.get("/health", tags=["Observability"])
async def health():
    return {
        "status": "healthy",
        "streams_active": len(registry.ids()),
        "uptime_seconds": round(time.monotonic() - _startup_time, 1),
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Runtime config (consumed by the browser for WebRTC signaling)
# ---------------------------------------------------------------------------


@app.get("/runtime-config.js", tags=["UI"])
async def runtime_config():
    payload = {
        "webrtcSignalingUrl": settings.WEBRTC_SIGNALING_URL,
        "webrtcSignalingPort": settings.WEBRTC_SIGNALING_PORT,
        "metricsServicePort": settings.METRICS_SERVICE_PORT,
        "vlmModel": settings.VLM_OVMS_MODEL,
        "vlmDevice": settings.VLM_DEVICE,
    }
    body = f"window.RUNTIME_CONFIG = {json.dumps(payload)};"
    return Response(content=body, media_type="application/javascript")


# ---------------------------------------------------------------------------
# Stream management
# ---------------------------------------------------------------------------


@app.get("/streams", tags=["Streams"])
async def list_streams():
    result = []
    for manager in registry.all():
        h = manager.get_health()
        result.append(
            {
                "stream_id": manager.stream_id,
                "url": manager.source_url,
                "alert_event": manager.alert_event,
                "publishing": h.publishing,
                "codec": h.codec,
                "resolution": h.resolution,
                "reconnect_count": h.reconnect_count,
                "whep_path": f"/{manager.stream_id}/whep",
                "caption": h.caption,
                "caption_ts": h.caption_ts,
                "ttft_ms": h.ttft_ms,
                "tpot_ms": h.tpot_ms,
                "throughput_tps": h.throughput_tps,
            }
        )
    return {"streams": result}


@app.post("/streams", tags=["Streams"])
async def add_stream(payload: dict):
    source_url = (payload or {}).get("url", "").strip()
    stream_id = (payload or {}).get("stream_id", "").strip() or "default"
    alert_event = (payload or {}).get("alert_event", "")
    normalized_alert_event = ""
    if isinstance(alert_event, str):
        normalized_alert_event = " ".join(alert_event.strip().split())
    prompt = ""
    if not source_url:
        raise HTTPException(status_code=400, detail="'url' is required")
    if settings.VLM_ENABLED:
        if not isinstance(alert_event, str) or not alert_event.strip():
            raise HTTPException(
                status_code=400,
                detail="'alert_event' is required when VLM is enabled",
            )
        try:
            prompt = build_alert_prompt(normalized_alert_event)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    try:
        registry.add(stream_id, source_url, prompt, normalized_alert_event)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"status": "added", "stream_id": stream_id}


@app.delete("/streams/{stream_id}", tags=["Streams"])
async def delete_stream(stream_id: str):
    try:
        registry.remove(stream_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Stream '{stream_id}' not found")
    return {"status": "removed", "stream_id": stream_id}


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse, tags=["UI"])
async def index():
    ui_path = os.path.join(_UI_DIR, "index.html")
    if not os.path.exists(ui_path):
        return HTMLResponse(content="<h1>UI not found</h1>", status_code=404)
    with open(ui_path, encoding="utf-8") as fh:
        return HTMLResponse(content=fh.read())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.PORT,
        log_level=settings.LOG_LEVEL.lower(),
    )
