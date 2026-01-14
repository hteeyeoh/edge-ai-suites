from typing import Optional
from pydantic import BaseModel, Field
from ..config import AGENT_MODE

# Default prompts based on mode
DEFAULT_PROMPT = (
    "Is there an accident in the stream? Just Answer with a Yes or No"
    if AGENT_MODE
    else "Describe what you see in one sentence."
)


class StartRunRequest(BaseModel):
    rtspUrl: str = Field(..., min_length=1)
    prompt: str = Field(default=DEFAULT_PROMPT)
    detectionModelName: Optional[str] = Field(default="yolov8s")
    detectionThreshold: Optional[float] = Field(default=0.5, ge=0.0, le=1.0)
    modelName: str = Field(default="OpenGVLab/InternVL2-2B")
    maxNewTokens: int = Field(default=70, ge=1, le=4096)
    pipelineName: Optional[str] = Field(default=None)
    runName: Optional[str] = Field(default=None)
