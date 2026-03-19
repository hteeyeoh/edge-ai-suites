# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

from ..config import (
    LLM_MODEL_ID,
    LLM_DEVICE,
    MAX_TOKENS,
    CACHE_DIR,
)
from ..logger import logger
from .embedding import CaptionEmbeddings
from .prompt import get_prompt_template
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_huggingface import HuggingFacePipeline
import os
import json
import asyncio


llm = HuggingFacePipeline.from_model_id(
                model_id = os.path.join(CACHE_DIR, LLM_MODEL_ID),
                task = "text-generation",
                backend = "openvino",
                model_kwargs = {
                    "device": LLM_DEVICE,
                    "ov_config": {
                        "PERFORMANCE_HINT": "LATENCY",
                        "NUM_STREAMS": "1",
                        "CACHE_DIR": os.path.join(CACHE_DIR, LLM_MODEL_ID, "model_cache"),
                    },
                    "trust_remote_code": True,
                },
                pipeline_kwargs = {"max_new_tokens": MAX_TOKENS},
            )

if llm.pipeline.tokenizer.eos_token_id:
    llm.pipeline.tokenizer.pad_token_id = llm.pipeline.tokenizer.eos_token_id

template = get_prompt_template(LLM_MODEL_ID)
prompt = ChatPromptTemplate.from_template(template)

caption_embeddings = CaptionEmbeddings()

async def process_embeddings(image_data: str, metadata: dict):
    """
    Process incoming embedding requests by adding the image-caption pair to the VDMS vector store.
    """
    # Offload synchronous network and vector DB writes to a thread to avoid
    # blocking the event loop in async API handlers.
    ids = await asyncio.to_thread(
        caption_embeddings.process_embeddings,
        image_data,
        metadata,
    )
    return ids

def default_context(docs):
    """
    Default context function that concatenates retrieved documents into a single string.
    This function is used when no retriever is provided to the chain, allowing for a simple context construction by joining the content of the retrieved documents.
    """
    return ""


def build_chain():
    """
    Build a LangChain chain that combines the retriever, prompt template, and LLM for processing queries.
    """
    retriever = caption_embeddings.get_retriever()

    if retriever:
        context = retriever | (
            lambda docs: "\n\n".join(doc.page_content for doc in docs)
        )
    else:
        context = default_context

    chain = (
        {
            "context": context,
            "question": RunnablePassthrough(),
        }
        | prompt
        | llm
        | StrOutputParser()
    )

    return chain


async def process_query(chain=None, query: str = ""):
    # Retrieve documents first (so metadata is available immediately)
    # retriever is a Runnable in LangChain, so `.ainvoke` works in async contexts
    retriever = caption_embeddings.get_retriever()

    docs = await retriever.ainvoke(query)

    # Example of sources data: [{'metadata': {'frame_data': 'base64_encoded', 'frame_format': 'BGRA', 'frame_height': 1080, 'frame_id': 11, 'frame_width': 1920}, 'preview': '<caption_text>'}, {'metadata': {'frame_data': 'base64_encoded', 'frame_format': 'BGRA', 'frame_height': 1080, 'frame_id': 10, 'frame_width': 1920}, 'preview': '<caption_text>'}, {'metadata': {'frame_data': 'base64_encoded', 'frame_format': 'BGRA', 'frame_height': 1080, 'frame_id': 4, 'frame_width': 1920}, 'preview': '<caption_text>'}]
    sources = [
       {
           "metadata": d.metadata,
            # optional: include a preview/snippet for UX
           "preview": d.page_content[:200],
       }
       for d in docs
    ]

    async for chunk in chain.astream(query):
        yield f"data: {chunk}\n\n"

    # Done marker
    yield "event: frame\n"
    yield f"data: {json.dumps(sources)}\n\n"
