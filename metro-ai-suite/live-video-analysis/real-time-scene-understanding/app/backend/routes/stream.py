# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from fastapi import APIRouter
from fastapi import HTTPException


def build_stream_router(registry, alert_index) -> APIRouter:
    """Builds a stream management router for the FastAPI application"""
    router = APIRouter(prefix="/api", tags=["stream"])

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
                    "alert_prompt": manager.vlm_prompt,
                    "deep_analyzer_prompt": manager.deep_analyzer_prompt,
                    "publishing": act_stream.publishing,
                    "codec": act_stream.codec,
                    "resolution": act_stream.resolution,
                    "reconnect_count": act_stream.reconnect_count,
                    "whep_path": f"/{manager.stream_id}/whep",
                    "caption": act_stream.caption,
                    "caption_history": act_stream.caption_history,
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
        alert_prompt = (payload or {}).get("alert_prompt", "")
        deep_analyzer_prompt = (payload or {}).get("deep_analyzer_prompt", "")
        normalized_alert_prompt = ""
        normalized_deep_analyzer_prompt = ""
        if isinstance(alert_prompt, str):
            normalized_alert_prompt = alert_prompt.strip()
        if isinstance(deep_analyzer_prompt, str):
            normalized_deep_analyzer_prompt = deep_analyzer_prompt.strip()
        if not source_url:
            raise HTTPException(status_code=400, detail="'url' is required")
        if not isinstance(alert_prompt, str) or not alert_prompt.strip():
            raise HTTPException(
                status_code=400,
                detail="'alert_prompt' is required",
            )
        if not isinstance(deep_analyzer_prompt, str) or not deep_analyzer_prompt.strip():
            raise HTTPException(
                status_code=400,
                detail="'deep_analyzer_prompt' is required",
            )

        try:
            registry.add(
                stream_id,
                source_url,
                normalized_alert_prompt,
                normalized_deep_analyzer_prompt,
            )
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
