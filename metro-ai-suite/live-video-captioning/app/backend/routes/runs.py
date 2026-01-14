import asyncio
import json
import re
import time
import uuid
from pathlib import Path
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ..config import AGENT_MODE, PIPELINE_NAME, PIPELINE_SERVER_URL, POLL_INTERVAL
from ..models import RunInfo, StartRunRequest
from ..models.requests import DEFAULT_PROMPT
from ..services import http_json, read_latest_line
from ..state import RUNS

router = APIRouter(prefix="/api", tags=["runs"])


@router.post("/runs")
async def start_run(req: StartRunRequest) -> RunInfo:
    """Start a new video captioning run."""
    # Process optional runName - use it for run_id if provided
    run_name = None
    if req.runName and req.runName.strip():
        # Sanitize: replace spaces with underscores, remove special chars
        sanitized = re.sub(r'\s+', '_', req.runName.strip())
        sanitized = re.sub(r'[^a-zA-Z0-9_-]', '', sanitized)
        if sanitized:
            run_name = sanitized
            # Check for duplicates and append suffix if needed
            base_name = sanitized
            counter = 1
            while run_name in RUNS:
                run_name = f"{base_name}_{counter}"
                counter += 1

    # Use runName for run_id if provided, otherwise generate UUID
    if run_name:
        run_id = run_name
    else:
        run_id = uuid.uuid4().hex[:10]

    peer_id = f"stream-{run_id[:10] if len(run_id) > 10 else run_id}"
    metadata_file = f"/tmp/results-{run_id[:10] if len(run_id) > 10 else run_id}.jsonl"

    pipeline_name = (req.pipelineName or PIPELINE_NAME).strip() or PIPELINE_NAME

    start_url = f"{PIPELINE_SERVER_URL.rstrip('/')}/pipelines/user_defined_pipelines/{pipeline_name}"
    payload = {
        "source": {"uri": req.rtspUrl, "type": "uri"},
        "destination": {
            "metadata": {"type": "file", "path": metadata_file, "format": "json-lines"},
            "frame": {"type": "webrtc", "peer-id": peer_id, "bitrate": 5000},
        },
        "parameters": {
            "captioner-prompt": (req.prompt or "").strip()
            or DEFAULT_PROMPT,
            "captioner_model_name": (req.modelName or "").strip()
            or "OpenGVLab/InternVL2-2B",
            "captioner_max_new_tokens": req.maxNewTokens,
            "detection_model_name": (req.detectionModelName or "").strip()
            or "yolov8s",
            "detection_threshold": req.detectionThreshold,
            "metadata-save-path": metadata_file,
        },
    }

    raw = http_json("POST", start_url, payload=payload)
    pipeline_id = raw.replace('"', "").strip()
    if not pipeline_id:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Pipeline server returned empty pipeline id",
                "body": raw,
            },
        )

    model_name = (req.modelName or "").strip() or "InternVL2-2B"
    # Use full run_id for custom names, truncated for UUID-based
    final_run_id = run_id if run_name else run_id[:10]
    info = RunInfo(
        runId=final_run_id,
        pipelineId=pipeline_id,
        peerId=peer_id,
        metadataFile=metadata_file,
        modelName=model_name,
        pipelineName=pipeline_name,
        runName=run_name,
        prompt=(req.prompt or "").strip() or DEFAULT_PROMPT,
        maxTokens=req.maxNewTokens,
        rtspUrl=req.rtspUrl,
    )
    RUNS[info.runId] = info
    return info


@router.get("/runs")
async def list_runs() -> list[RunInfo]:
    """List all active runs."""
    return list(RUNS.values())


async def _multiplexed_metadata_generator() -> AsyncGenerator[str, None]:
    """Generator that reads metadata from all active runs and multiplexes into a single SSE stream."""
    last_payloads: dict[str, str] = {}
    last_modified_times: dict[str, float] = {}

    while True:
        try:
            # Get current list of runs (defensive copy)
            current_runs = dict(RUNS)

            for run_id, info in current_runs.items():
                path = Path(info.metadataFile)

                # Check if file exists and get its modification time
                if not path.exists():
                    continue

                try:
                    current_mtime = path.stat().st_mtime
                    last_mtime = last_modified_times.get(run_id, 0)

                    # Only read file if it was modified since last check
                    if current_mtime > last_mtime:
                        latest = read_latest_line(path)
                        if latest and latest != last_payloads.get(run_id):
                            last_payloads[run_id] = latest
                            last_modified_times[run_id] = current_mtime

                            # Wrap the data with runId for client-side demultiplexing
                            # Add received_at timestamp for lag calculation
                            received_at = time.time()
                            try:
                                data_obj = json.loads(latest)
                                envelope = {"runId": run_id, "data": data_obj, "received_at": received_at}
                            except json.JSONDecodeError:
                                envelope = {"runId": run_id, "data": latest, "received_at": received_at}

                            yield f"data: {json.dumps(envelope)}\n\n"
                except OSError:
                    # File might have been deleted or is inaccessible
                    continue

            # Send heartbeat to keep connection alive
            yield f": heartbeat\n\n"

            # Clean up stale entries
            current_run_ids = set(current_runs.keys())
            stale_ids = [rid for rid in last_payloads if rid not in current_run_ids]
            for rid in stale_ids:
                last_payloads.pop(rid, None)
                last_modified_times.pop(rid, None)

        except Exception as e:
            # Log error but don't break the generator
            print(f"Error in multiplexed metadata generator: {e}")
            yield f": error - {e}\n\n"

        await asyncio.sleep(POLL_INTERVAL)


@router.get("/runs/metadata-stream")
async def multiplexed_metadata_stream() -> StreamingResponse:
    """Multiplexed SSE stream that provides metadata for all active runs."""
    print("Multiplexed metadata stream requested")
    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Cache-Control",
    }
    return StreamingResponse(
        _multiplexed_metadata_generator(),
        media_type="text/event-stream",
        headers=headers,
    )


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> RunInfo:
    """Get details of a specific run."""
    info = RUNS.get(run_id)
    if not info:
        raise HTTPException(status_code=404, detail={"message": "Run not found"})
    return info


@router.delete("/runs/{run_id}")
async def stop_run(run_id: str) -> dict[str, str]:
    """Stop a running pipeline."""
    info = RUNS.get(run_id)
    if not info:
        raise HTTPException(status_code=404, detail={"message": "Run not found"})
    stop_url = f"{PIPELINE_SERVER_URL.rstrip('/')}/pipelines/{info.pipelineId}"

    # Try to stop pipeline on backend, but always remove from internal list
    # A failure (502) usually means the pipeline is already stopped
    try:
        http_json("DELETE", stop_url)
    except HTTPException:
        # Pipeline may already be stopped or unreachable - continue cleanup
        pass

    RUNS.pop(run_id, None)
    try:
        Path(info.metadataFile).unlink(missing_ok=True)
    except OSError:
        pass
    return {"status": "stopped", "runId": run_id}
