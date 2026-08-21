# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from fastapi import APIRouter


def build_deep_metrics_router(alert_index, get_deep_analyzer) -> APIRouter:
    """Build the stream-level deep-analyzer metrics router."""
    router = APIRouter(tags=["Deep Analyzer Metrics"])

    @router.get("/streams/{stream_id}/deep-metrics", summary="Get deep-analyzer metrics")
    async def deep_metrics(stream_id: str) -> dict:
        records, total = alert_index.list(stream_id, limit=1)
        latest = records[0] if records else {}
        metrics = latest.get("metrics", {}) if isinstance(latest, dict) else {}
        if not isinstance(metrics, dict):
            metrics = {}
        job_metrics = get_deep_analyzer().get_metrics(stream_id)

        return {
            "stream_id": stream_id,
            "deep_segments_completed": job_metrics["completed"],
            "deep_segments_persisted": total,
            "deep_analyzer_model": latest.get("model", "unknown"),
            "deep_analyzer_device": latest.get("device", "unknown"),
            "deep_last_completed_at": latest.get("uploaded_at", ""),
            "deep_ttft_ms": metrics.get("ttft_ms"),
            "deep_tpot_ms": metrics.get("tpot_ms"),
            "deep_throughput_tps": metrics.get("throughput_tps"),
            "deep_total_duration_ms": metrics.get("total_duration_ms"),
            "deep_frames_sampled": metrics.get("frames_sampled"),
            "deep_total_tokens_generated": metrics.get("total_tokens_generated"),
            "deep_segments_submitted": job_metrics["submitted"],
            "deep_segments_queued": job_metrics["queued"],
            "deep_segments_in_flight": job_metrics["in_flight"],
            "deep_segments_active": job_metrics["active"],
            "deep_segments_failed": job_metrics["failed"],
            "deep_segments_max_in_flight": job_metrics["max_in_flight"],
        }

    return router