from fastapi import APIRouter

from ..models import PipelineList
from ..services import discover_pipelines_remote

router = APIRouter(prefix="/api", tags=["pipelines"])


@router.get("/pipelines", response_model=PipelineList)
async def list_pipelines() -> PipelineList:
    """List available pipelines from the pipeline server."""
    names = discover_pipelines_remote()
    return PipelineList(pipelines=names)
