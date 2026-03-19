# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

from langchain_vdms.vectorstores import VDMS, VDMS_Client
from typing import Any, Dict
import logging
import requests
import uuid

from ..config import (
    EMBEDDING_HOST,
    EMBEDDING_HOST_PORT,
    EMBEDDING_MODEL,
    VDMS_HOST,
    VDMS_PORT,
    TOP_K,
)
from .embedding_wrapper import EmbeddingAPI

logger = logging.getLogger("app.embedding")

class CaptionEmbeddings:
    """
    Caption Embeddings service that interfaces with VDMS to store
    image-caption pairs.
    """

    def __init__(self):

        self.embedding_endpoint = f"http://{EMBEDDING_HOST}:{EMBEDDING_HOST_PORT}/embeddings"

        logger.info(f"Initializing CaptionEmbeddings with embedding endpoint: {self.embedding_endpoint}")
        # Initialize embedding resources
        embeddings = EmbeddingAPI(
            api_url=self.embedding_endpoint,
            model_name=EMBEDDING_MODEL
        )

        vector_dimensions = embeddings.get_embedding_length()

        logger.info(f"VDMS_HOST: {VDMS_HOST}, VDMS_PORT: {VDMS_PORT}")
        self.vdms_client = VDMS_Client(
            host = VDMS_HOST,
            port = VDMS_PORT,
        )

        self.vdms_store = VDMS(
            client=self.vdms_client,
            embedding=embeddings,
            collection_name="captions_collection",
            engine="FaissFlat",
            distance_strategy="IP",
            embedding_dimensions=vector_dimensions,
        )

        self._http = requests.Session()
        self._http.headers.update({'Content-Type': 'application/json'})

    @staticmethod
    def _build_embedding_metadata(img_blob: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Extract and normalize metadata stored with the vector payload."""

        resolution = metadata.get("resolution") or {}

        return {
            "frame_id": metadata.get("frame_id", ""),
            "frame_format": metadata.get("img_format", ""),
            "frame_width": resolution.get("width"),
            "frame_height": resolution.get("height"),
            "frame_data": img_blob,
        }

    def process_embeddings(self, img_blob: str, metadata: Dict[str, Any] = None):
        """
        Add an image-caption pair to the VDMS vector store.
        """
        metadata = metadata or {}
        caption_text = str(metadata.get("result", "")).strip()

        if not caption_text:
            raise ValueError("Metadata field 'result' is required and cannot be empty.")

        payload = {
            "input": {"type": "text", "text": caption_text},
            "model": EMBEDDING_MODEL,
            "encoding_format": "float"
        }

        resp = self._http.post(self.embedding_endpoint, json=payload, timeout=(1.0, 15.0))
        resp.raise_for_status()
        rj = resp.json()
        emb = rj.get("embedding")

        if emb is None:
            raise ValueError("Missing 'embedding' in response")

        if not isinstance(emb, (list, tuple)) or not emb:
            raise TypeError(f"Embedding must be a non-empty list/tuple, got {type(emb)}")

        vector = [float(x) for x in emb]
        emb_metadata = self._build_embedding_metadata(img_blob, metadata)
        ids = str(uuid.uuid4())

        self.vdms_store.add_from(
            texts=[caption_text],
            metadatas=[emb_metadata],
            embeddings=[vector],
            ids=[ids],
        )

        return ids

    def get_retriever(self):
        """
        Return a LangChain retriever object for querying the VDMS store.
        """

        retriever = self.vdms_store.as_retriever(
            search_type="similarity",
            search_kwargs={"k": TOP_K},
        )

        return retriever