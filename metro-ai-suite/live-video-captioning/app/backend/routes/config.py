import json
from fastapi import APIRouter, Response
from ..config import AGENT_MODE, DEFAULT_RTSP_URL, METADATA_FILE, PEER_ID, SIGNALING_URL, ENABLE_DETECTION_PIPELINE
from ..models.requests import DEFAULT_PROMPT

router = APIRouter()


@router.get("/runtime-config.js")
async def runtime_config() -> Response:
    """Return runtime configuration as JavaScript for the frontend."""
    payload = {
        "signalingUrl": SIGNALING_URL,
        "defaultPeerId": PEER_ID,
        "defaultMetadataFile": METADATA_FILE,
        "agentMode": AGENT_MODE,
        "defaultPrompt": DEFAULT_PROMPT,
        "defaultRtspUrl": DEFAULT_RTSP_URL,
        "enableDetectionPipeline": ENABLE_DETECTION_PIPELINE,
    }
    body = f"window.RUNTIME_CONFIG = {json.dumps(payload)};"
    return Response(content=body, media_type="application/javascript")
