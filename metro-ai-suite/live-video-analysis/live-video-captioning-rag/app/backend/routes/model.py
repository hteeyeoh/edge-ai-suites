# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

from ..config import LLM_MODEL_ID
from fastapi import APIRouter

router = APIRouter(prefix="/api", tags = ["models"])


@router.get("/model", response_model = dict)
async def get_llm_model():
    """
    Endpoint to retrieve LLM model details.
    """

    return {"status": "Success", "llm_model": LLM_MODEL_ID}