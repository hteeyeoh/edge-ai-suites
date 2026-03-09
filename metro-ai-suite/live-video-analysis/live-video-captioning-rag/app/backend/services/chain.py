# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

from ..config import (
    LLM_MODEL_ID,
    LLM_DEVICE,
    MAX_TOKENS,
    CACHE_DIR,
    EMBEDDING_HOST,
    EMBEDDING_HOST_PORT,
    EMBEDDING_MODEL,
    VDMS_HOST,
    VDMS_PORT,
    TOP_K,
)
from ..logger import logger
from .embeddings import EmbeddingAPI
from .prompt import get_prompt_template
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_huggingface import HuggingFacePipeline
from langchain_vdms.vectorstores import VDMS, VDMS_Client
import os
import json


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

logger.info(f"VDMS_HOST: {VDMS_HOST}, VDMS_PORT: {VDMS_PORT}")
vdms_client = VDMS_Client(host=VDMS_HOST, port=VDMS_PORT)

def default_context(docs):
    """
    Default context function that concatenates retrieved documents into a single string.
    This function is used when no retriever is provided to the chain, allowing for a simple context construction by joining the content of the retrieved documents.
    """

    return ""


def get_retriever():
    """
    Initialize VDMS retriever and return as a LangChain retriever object
    """

    embedding_api_url = f"http://{EMBEDDING_HOST}:{EMBEDDING_HOST_PORT}/embeddings"
    logger.info(f"embedding_URL: {embedding_api_url}")

    embeddings = EmbeddingAPI(
        api_url=embedding_api_url,
        model_name=EMBEDDING_MODEL,
    )

    vector_dimensions = embeddings.get_embedding_length()

    vdms_store = VDMS(
        client=vdms_client,
        embedding=embeddings,
        collection_name="captions_collection",
        engine="FaissFlat",
        distance_strategy="IP",
        embedding_dimensions=vector_dimensions
    )

    retriever = vdms_store.as_retriever(
        search_type="similarity",
        search_kwargs={"k": TOP_K},
    )

    return retriever


def build_chain(retriever=None):
    """
    Build a LangChain chain that combines the retriever, prompt template, and LLM for processing queries.
    """

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


async def process_query(chain=None, query: str = "", retriever=None):
    # Retrieve documents first (so metadata is available immediately)
    # retriever is a Runnable in LangChain, so `.ainvoke` works in async contexts
    docs = await retriever.ainvoke(query)

    # Example of sources data: [{'metadata': {'frame_data': 'base64_encoded', 'frame_format': 'BGRA', 'frame_height': 1080, 'frame_id': 11, 'frame_width': 1920}, 'preview': 'A white Nissan Leaf car is parked in a parking garage.'}, {'metadata': {'frame_data': 'base64_encoded', 'frame_format': 'BGRA', 'frame_height': 1080, 'frame_id': 10, 'frame_width': 1920}, 'preview': 'A white Nissan Leaf car is parked in a parking garage with its tail lights on, surrounded by marked spaces.'}, {'metadata': {'frame_data': 'base64_encoded', 'frame_format': 'BGRA', 'frame_height': 1080, 'frame_id': 4, 'frame_width': 1920}, 'preview': 'A white Nissan Leaf car is parked in a parking garage with its tail lights on, surrounded by marked spaces.'}]
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
