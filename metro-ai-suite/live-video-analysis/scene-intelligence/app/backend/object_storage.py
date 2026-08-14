# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""SeaweedFS (S3-compatible) object storage helper for deep-analysis artifacts."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.config import settings

logger = logging.getLogger(__name__)


class SeaweedFSStorage:
    """Encapsulates SeaweedFS S3 operations and bucket lifecycle.

    TODO: no retention/expiry policy yet — uploaded segment videos and
    sidecar JSON accumulate in the bucket forever. Add lifecycle rules
    (e.g. SeaweedFS TTL/collection settings or a periodic cleanup pass)
    once storage growth needs to be bounded.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._client = None
        self._bucket_ready = False
        # Runs the video and sidecar uploads concurrently instead of back-to-back.
        self._upload_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="seaweedfs-upload")

        self._client = self._create_client()
        # Pre-create/verify the bucket once during initialization.
        self._ensure_bucket()

    def _create_client(self):
        import boto3
        from botocore.config import Config

        return boto3.client(
            "s3",
            endpoint_url=settings.SEAWEEDFS_ENDPOINT_URL,
            aws_access_key_id=settings.SEAWEEDFS_ACCESS_KEY,
            aws_secret_access_key=settings.SEAWEEDFS_SECRET_KEY,
            use_ssl=settings.SEAWEEDFS_USE_SSL,
            verify=settings.SEAWEEDFS_VERIFY_SSL,
            config=Config(max_pool_connections=settings.SEAWEEDFS_MAX_POOL_CONNECTIONS),
        )

    @staticmethod
    def _backoff_delay(attempt: int) -> float:
        delay = settings.SEAWEEDFS_RETRY_DELAY_SECONDS * attempt
        return min(delay, settings.SEAWEEDFS_MAX_RETRY_DELAY_SECONDS)

    def _run_with_retries(self, fn, operation_name: str):
        last_exc = None
        for attempt in range(1, settings.SEAWEEDFS_UPLOAD_RETRIES + 1):
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001 - keep deep-analysis resilient
                last_exc = exc
                if attempt >= settings.SEAWEEDFS_UPLOAD_RETRIES:
                    break
                logger.warning(
                    "SeaweedFS %s failed (attempt %d/%d), retrying: %s",
                    operation_name,
                    attempt,
                    settings.SEAWEEDFS_UPLOAD_RETRIES,
                    exc,
                )
                time.sleep(self._backoff_delay(attempt))
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"SeaweedFS {operation_name} failed")

    @staticmethod
    def _to_s3_metadata_value(value: Any) -> str:
        text = str(value)
        # Header-safe metadata values can't contain newlines/tabs; only normalize if present.
        if any(c in text for c in ("\n", "\r", "\t")):
            return " ".join(text.split())
        return text

    @staticmethod
    def _is_bucket_missing_error(exc: Exception) -> bool:
        response = getattr(exc, "response", None)
        if not isinstance(response, dict):
            return False

        status_code = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        error_code = response.get("Error", {}).get("Code")
        if status_code == 404:
            return True
        return str(error_code) in {"404", "NoSuchBucket", "NotFound"}

    def _ensure_bucket(self) -> None:
        if self._bucket_ready:
            return

        with self._lock:
            if self._bucket_ready:
                return

            missing_bucket = False
            last_exc = None
            for attempt in range(1, settings.SEAWEEDFS_UPLOAD_RETRIES + 1):
                try:
                    self._client.head_bucket(Bucket=settings.SEAWEEDFS_BUCKET)
                    break
                except Exception as exc:  # noqa: BLE001 - keep bootstrap resilient
                    last_exc = exc
                    if self._is_bucket_missing_error(exc):
                        logger.info(
                            "SeaweedFS bucket '%s' not found; creating it now",
                            settings.SEAWEEDFS_BUCKET,
                        )
                        missing_bucket = True
                        break
                    if attempt >= settings.SEAWEEDFS_UPLOAD_RETRIES:
                        raise RuntimeError("SeaweedFS head_bucket failed") from exc
                    logger.warning(
                        "SeaweedFS head_bucket failed (attempt %d/%d), retrying: %s",
                        attempt,
                        settings.SEAWEEDFS_UPLOAD_RETRIES,
                        exc,
                    )
                    time.sleep(self._backoff_delay(attempt))

            if missing_bucket:
                def _create_bucket():
                    return self._client.create_bucket(Bucket=settings.SEAWEEDFS_BUCKET)

                try:
                    self._run_with_retries(_create_bucket, "create_bucket")
                    logger.info("SeaweedFS bucket created: %s", settings.SEAWEEDFS_BUCKET)
                except Exception as exc:
                    response = getattr(exc, "response", None)
                    error_code = None
                    if isinstance(response, dict):
                        error_code = str(response.get("Error", {}).get("Code"))
                    if error_code not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                        raise
                    logger.info(
                        "SeaweedFS bucket '%s' already exists during create race",
                        settings.SEAWEEDFS_BUCKET,
                    )

            self._bucket_ready = True

    def _build_object_keys(self, stream_id: str, segment_path: str, frame_id: uuid.UUID) -> tuple[str, str]:
        base_name = f"{Path(segment_path).stem}-{frame_id}"
        root = stream_id
        video_key = f"{root}/{base_name}.mp4"
        sidecar_key = f"{root}/{base_name}.analysis.json"
        return video_key, sidecar_key

    def upload_segment_and_metadata(
        self,
        *,
        stream_id: str,
        segment_path: str,
        alert_event: str,
        frame_id: uuid.UUID,
        description: str,
        metrics: dict[str, Any],
        deep_model: str,
        deep_device: str,
        trigger_caption: str = "",
    ) -> dict[str, Any] | None:
        if not os.path.isfile(segment_path):
            logger.warning("[%s] seaweedfs upload skipped; segment not found: %s", stream_id, segment_path)
            return None

        video_key, sidecar_key = self._build_object_keys(stream_id, segment_path, frame_id)
        metadata = {
            "stream_id": self._to_s3_metadata_value(stream_id),
            "frame_id": self._to_s3_metadata_value(frame_id),
            "alert_event": self._to_s3_metadata_value(alert_event),
            "deep_model": self._to_s3_metadata_value(deep_model),
            "deep_device": self._to_s3_metadata_value(deep_device),
            "analyzed_at": self._to_s3_metadata_value(datetime.now(timezone.utc).isoformat()),
            "frames_sampled": self._to_s3_metadata_value(metrics.get("frames_sampled", "")),
            "analysis_sidecar_key": sidecar_key,
        }

        def _upload_video() -> None:
            self._client.upload_file(
                Filename=segment_path,
                Bucket=settings.SEAWEEDFS_BUCKET,
                Key=video_key,
                ExtraArgs={
                    "ContentType": "video/mp4",
                    "Metadata": metadata,
                },
            )

        payload = {
            "stream_id": stream_id,
            "frame_id": str(frame_id),
            "segment_path": segment_path,
            "alert_event": alert_event,
            "trigger_caption": trigger_caption,
            "description": description,
            "metrics": metrics,
            "model": deep_model,
            "device": deep_device,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "video_object_key": video_key,
            "sidecar_key": sidecar_key,
        }
        sidecar_body = json.dumps(payload, ensure_ascii=True).encode("utf-8")

        def _upload_sidecar() -> None:
            self._client.put_object(
                Bucket=settings.SEAWEEDFS_BUCKET,
                Key=sidecar_key,
                Body=sidecar_body,
                ContentType="application/json",
            )

        video_future = self._upload_executor.submit(self._run_with_retries, _upload_video, "upload_video")
        sidecar_future = self._upload_executor.submit(self._run_with_retries, _upload_sidecar, "upload_sidecar")
        video_future.result()
        sidecar_future.result()

        logger.info(
            "[%s] seaweedfs upload complete bucket=%s key=%s sidecar=%s",
            stream_id,
            settings.SEAWEEDFS_BUCKET,
            video_key,
            sidecar_key,
        )
        return payload
