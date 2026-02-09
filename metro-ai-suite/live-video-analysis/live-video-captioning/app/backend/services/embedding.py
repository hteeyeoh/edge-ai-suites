# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

from langchain_vdms.vectorstores import VDMS, VDMS_Client
from langchain_core.embeddings import Embeddings
from typing import Any, Dict, List
import json
import logging
import requests
import uuid

from ..config import VDMS_HOST, VDMS_PORT, EMBEDDING_HOST, EMBEDDING_PORT, EMBEDDING_MODEL_NAME
from .embedding_wrapper import EmbeddingAPI

logger = logging.getLogger("app.embedding")


class DummyEmbeddings(Embeddings):
    """
    Minimal dummy embedding class that satisfies VDMS requirements.
    We won't actually use these methods since we use add_from() directly.
    """
    def __init__(self, dimensions: int = 512):
        self.dimensions = dimensions

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Won't be called since we use add_from() directly."""
        raise NotImplementedError("Use add_from() method instead")

    def embed_query(self, text: str) -> List[float]:
        """Won't be called since we use add_from() directly."""
        raise NotImplementedError("Use add_from() method instead")


class CaptionEmbeddings:
    """
    Caption Embeddings service that interfaces with VDMS to store
    image-caption pairs.
    """

    def __init__(self):

        self.embedding_endpoint = f"http://{EMBEDDING_HOST}:{EMBEDDING_PORT}/embeddings"

        # Initialize embedding resources
        embeddings = EmbeddingAPI(
            api_url=self.embedding_endpoint,
            model_name=EMBEDDING_MODEL_NAME
        )

        vector_dimensions = embeddings.get_embedding_length()

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

    def process_embeddings(self, img_blob: str, metadata: Dict[str, Any] = None):
        """
        Add an image-caption pair to the VDMS vector store.
        """
        caption_text = metadata.get("result", "")

        payload = {
            "input": {"type": "text", "text": caption_text},
            "model": EMBEDDING_MODEL_NAME,
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
        emb_metadata = {
            #"frame_resolution": metadata.get("resolution", ""),
            "frame_id": metadata.get("frame_id", ""),
            "frame_format": metadata.get("img_format", ""),
            "frame_width": metadata["resolution"]["width"],
            "frame_height": metadata["resolution"]["height"],
            "frame_data": img_blob,
        }
        ids = str(uuid.uuid4())

        self.vdms_store.add_from(
            texts=[caption_text],
            metadatas=[emb_metadata],
            embeddings=[vector],
            ids=[ids],
        )
        print(f"Added embedding with ID {ids} to VDMS.")

        return ids