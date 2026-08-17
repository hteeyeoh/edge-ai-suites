# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from backend.config import build_alert_prompt
from fastapi import APIRouter
from fastapi import HTTPException


def build_stream_router(registry, alert_index) -> APIRouter:
    """Builds a stream management router for the FastAPI application"""
    router = APIRouter(tags=["Stream"])

    @router.get("/streams", summary="List all active streams")
    async def list_streams() -> dict:
        """List all active streams"""
        result = []
        for manager in registry.all():
            act_stream = manager.get_health()
            result.append(
                {
                    "stream_id": manager.stream_id,
                    "url": manager.source_url,
                    "alert_event": manager.alert_event,
                    "publishing": act_stream.publishing,
                    "codec": act_stream.codec,
                    "resolution": act_stream.resolution,
                    "reconnect_count": act_stream.reconnect_count,
                    "whep_path": f"/{manager.stream_id}/whep",
                    "caption": act_stream.caption,
                    "caption_ts": act_stream.caption_ts,
                    "ttft_ms": act_stream.ttft_ms,
                    "tpot_ms": act_stream.tpot_ms,
                    "throughput_tps": act_stream.throughput_tps,
                    "alert_count": alert_index.count(manager.stream_id),
                }
            )
        return {"streams": result}

    @router.post("/streams", summary="Add a new stream")
    async def add_stream(payload: dict):
        """Add a new stream"""
        source_url = (payload or {}).get("url", "").strip()
        stream_id = (payload or {}).get("stream_id", "").strip() or "default"
        alert_event = (payload or {}).get("alert_event", "")
        normalized_alert_event = ""
        if isinstance(alert_event, str):
            normalized_alert_event = " ".join(alert_event.strip().split())
        prompt = ""
        if not source_url:
            raise HTTPException(status_code=400, detail="'url' is required")
        if not isinstance(alert_event, str) or not alert_event.strip():
            raise HTTPException(
                status_code=400,
                detail="'alert_event' is required",
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

    @router.delete("/streams/{stream_id}", summary="Delete a stream")
    async def delete_stream(stream_id: str):
        """Delete a stream"""
        try:
            registry.remove(stream_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Stream '{stream_id}' not found")
        return {"status": "removed", "stream_id": stream_id}

    return router
