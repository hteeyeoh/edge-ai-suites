from typing import Optional

from pydantic import BaseModel


class RunInfo(BaseModel):
    runId: str
    pipelineId: str
    peerId: str
    metadataFile: str
    modelName: Optional[str] = None
    pipelineName: Optional[str] = None
    runName: Optional[str] = None
    prompt: Optional[str] = None
    maxTokens: Optional[int] = None
    rtspUrl: Optional[str] = None


class ModelList(BaseModel):
    models: list[str]


class PipelineList(BaseModel):
    pipelines: list[str]
