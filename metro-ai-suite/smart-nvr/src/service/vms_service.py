# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
import asyncio
import requests
import os
import time
import tempfile
import subprocess
import aiofiles
import logging
from pathlib import Path
from typing import Optional
from fastapi import HTTPException
from api.endpoints.frigate_api import FrigateService
from model.model import Sampling, Evam, SummaryPayload
from api.endpoints.summarization_api import SummarizationService
from config import VSS_SUMMARY_URL
from config import VSS_SEARCH_URL

# Initialize logger
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,  # Set to DEBUG if you want more verbose logs
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

frigate_service = FrigateService()
summarization_service = SummarizationService()
FRIGATE_CLIP_END_BUFFER_SECONDS = 2.0
FRIGATE_CLIP_MAX_WAIT_SECONDS = 5.0


class VmsService:
    def __init__(self, frigate_service, summarization_service):
        self.frigate_service = frigate_service
        self.summarization_service = summarization_service
        self.vss_summary_url: str = VSS_SUMMARY_URL
        self.vss_search_url: str = VSS_SEARCH_URL
        logger.info("VmsService initialized.")

    def _build_camera_candidates(self, camera_name: str, available_cameras: list[str]) -> list[str]:
        """Build an ordered candidate list for clip retrieval camera names."""
        candidates: list[str] = []

        def _add(name: Optional[str]):
            if not name:
                return
            cleaned = str(name).strip()
            if cleaned and cleaned not in candidates:
                candidates.append(cleaned)

        # Region-stream synthetic names never exist in Frigate, so derive the real
        # camera name first and skip the guaranteed-to-fail synthetic lookup.
        derived_real_name: Optional[str] = None
        if camera_name.endswith("-region-region"):
            base = camera_name[: -len("-region-region")]
            derived_real_name = f"{base}-camera1"
        elif camera_name.endswith("-region"):
            base = camera_name[: -len("-region")]
            derived_real_name = f"{base}-camera1"
        elif "-region-" in camera_name:
            derived_real_name = camera_name.replace("-region-", "-", 1)

        if derived_real_name:
            _add(derived_real_name)
        else:
            _add(camera_name)

        if available_cameras:
            # Prefer cameras sharing the same SI prefix (e.g. si1-*).
            base_prefix = camera_name.split("-region")[0].split("-")[0]
            for cam in available_cameras:
                if cam.startswith(f"{base_prefix}-"):
                    _add(cam)

            # If nothing matched suffix rules, at least try available cameras deterministically.
            for cam in available_cameras:
                _add(cam)

        return candidates

    async def upload_video_to_summarizer(
        self,
        camera_name: str,
        start_time: float,
        end_time: float,
        is_search: bool,
        upload_tag: Optional[str] = None,
        apply_end_buffer: bool = False,
    ) -> dict:
        """Fetches clip from Frigate, writes to temp file, uploads it, and returns videoId."""
        available_cameras: list[str] = []
        try:
            camera_map = await asyncio.to_thread(self.frigate_service.get_camera_names)
            available_cameras = sorted(list((camera_map or {}).keys()))
        except Exception as e:
            logger.debug(f"Could not fetch Frigate camera list for fallback resolution: {e}")

        camera_candidates = self._build_camera_candidates(camera_name, available_cameras)
        logger.info(
            f"Clip retrieval camera candidates for requested camera '{camera_name}': {camera_candidates}"
        )

        retrieval_start_time = float(start_time)
        retrieval_end_time = float(end_time)
        if apply_end_buffer:
            # Wait for the window to pass rather than shrinking it: clamping the end time
            # can cut off the window before the event itself when processed near-real-time.
            ready_at = retrieval_end_time + FRIGATE_CLIP_END_BUFFER_SECONDS
            wait_seconds = min(ready_at - time.time(), FRIGATE_CLIP_MAX_WAIT_SECONDS)
            if wait_seconds > 0:
                logger.info(
                    f"Delaying clip retrieval by {wait_seconds:.2f}s so Frigate can finalize "
                    f"the recording (start={retrieval_start_time}, end={retrieval_end_time})."
                )
                await asyncio.sleep(wait_seconds)

        tmp_path = ""
        temp_file_size = 0
        selected_camera = None

        for candidate_camera in camera_candidates:
            try:
                stream_response = await asyncio.to_thread(
                    self.frigate_service.get_clip_from_timestamps,
                    candidate_camera,
                    retrieval_start_time,
                    retrieval_end_time,
                    download=True,
                )
                logger.info(f"Clip retrieved from Frigate for camera '{candidate_camera}'.")
            except Exception as e:
                logger.warning(
                    f"Failed to get clip for camera '{candidate_camera}' "
                    f"(start={retrieval_start_time}, end={retrieval_end_time}): {e}"
                )
                continue

            temp_file_size = 0
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_file:
                tmp_path = tmp_file.name
            logger.info(f"Temporary file created at: {tmp_path}")

            try:
                async with aiofiles.open(tmp_path, "wb") as f:
                    async for chunk in stream_response.body_iterator:
                        await f.write(chunk)
                        temp_file_size += len(chunk)
            except Exception as e:
                logger.error(f"Failed to process video stream for camera '{candidate_camera}': {e}")
                try:
                    if tmp_path and os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except Exception:
                    pass
                return {"status": 500, "message": "Failed to process video stream"}

            # Check if video is too small (likely empty)
            if temp_file_size <= 100:
                logger.warning(
                    f"No video found for camera '{candidate_camera}' and given timestamps (file size: {temp_file_size} bytes)"
                )
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
                tmp_path = ""
                continue

            selected_camera = candidate_camera
            logger.info(
                f"Stream written to temporary file for camera '{selected_camera}'. Size: {temp_file_size} bytes"
            )
            break

        if not selected_camera:
            return {
                "status": 404,
                "message": "No video footage available for the selected time range. Please try different timestamps.",
            }

        # Upload file
        try:
            if is_search:
                upload_result = await asyncio.to_thread(
                    self.summarization_service.video_upload,
                    tmp_path,
                    self.vss_search_url,
                    upload_tag or selected_camera,
                )
            else:
                upload_result = await asyncio.to_thread(
                    self.summarization_service.video_upload,
                    tmp_path,
                    self.vss_summary_url,
                    upload_tag or selected_camera,
                )

            if not upload_result or "videoId" not in upload_result:
                return {
                    "status": 500,
                    "message": "Video upload failed - no videoId returned",
                }

            logger.info(f"Video uploaded, videoId: {upload_result.get('videoId')}")
            return {"status": 200, "message": upload_result["videoId"]}
        except Exception as e:
            logger.error(f"Video upload failed: {e}")
            return {"status": 500, "message": "Video upload failed"}
        finally:
            try:
                if os.path.exists(tmp_path):
                    logger.info(f"Cleaning up temporary file: {tmp_path}")
                    os.remove(tmp_path)
            except Exception as e:
                logger.warning(f"Failed to remove temporary file: {e}")

    async def summarize(
        self,
        camera_name: str,
        start_time: float,
        end_time: float,
        upload_tag: Optional[str] = None,
        apply_end_buffer: bool = False,
    ) -> dict:
        logger.info(
            f"Starting summarization for camera: {camera_name}, "
            f"start_time: {start_time}, end_time: {end_time}"
        )

        upload_resp = await self.upload_video_to_summarizer(
            camera_name,
            start_time,
            end_time,
            False,
            upload_tag=upload_tag,
            apply_end_buffer=apply_end_buffer,
        )
        if upload_resp["status"] != 200:
            return upload_resp

        try:
            payload = SummaryPayload(
                videoId=upload_resp["message"],
                title=f"summary_{camera_name}_{int(start_time)}",
                sampling=Sampling(chunkDuration=8, samplingFrame=8),
                evam=Evam(evamPipeline="object_detection"),
            )
            pipeline = await asyncio.to_thread(
                self.summarization_service.create_summary,
                payload,
                self.vss_summary_url,
            )

            if not pipeline or "summaryPipelineId" not in pipeline:
                return {
                    "status": 500,
                    "message": "Summary creation failed - no pipelineId returned",
                }

            logger.info(
                f"Summary pipeline created with ID: {pipeline.get('summaryPipelineId')}"
            )
            return {"status": 200, "message": pipeline["summaryPipelineId"]}
        except Exception as e:
            logger.error(f"Failed to create summary: {e}")
            return {"status": 500, "message": "Failed to create video summary"}

    def summary(self, summary_id: str):
        logger.info(f"Fetching summary result for ID: {summary_id}")
        try:
            result = summarization_service.get_summary_result(
                summary_id, self.vss_summary_url
            )
        except Exception as e:
            logger.error(
                f"Failed to retrieve summary from summarization service for summary id {summary_id}: {e}"
            )
            raise

        video_summary = result.get("summary")

        # If summary is empty or None, return fallback structure
        if not video_summary:
            logger.info("Final summary not ready yet.")
            # Extract summarized frames with fallback structure
            frame_summaries = result.get("frameSummaries", [])
            simplified_frame_summaries = []

            for frame in frame_summaries:
                simplified_frame_summaries.append(
                    {
                        "startFrame": frame.get("startFrame"),
                        "endFrame": frame.get("endFrame"),
                        "status": frame.get("status"),
                        "summary": frame.get("summary"),
                    }
                )

            return {
                "summary": "Final summary is being generated please wait for a while.",
                "frameSummaries": simplified_frame_summaries,
            }

        logger.info("Summary retrieved successfully.")
        return {"summary": video_summary}

    async def search_embeddings(
        self,
        camera_name: str,
        start_time: float,
        end_time: float,
        upload_tag: Optional[str] = None,
        apply_end_buffer: bool = False,
    ) -> dict:
        """
        Uploads video from the specified camera and time range,
        then triggers the search-embeddings API.

        Returns:
            str: Message from the search-embeddings API response.
        """
        logger.info(
            f"Starting search_embeddings for camera={camera_name}, start={start_time}, end={end_time}"
        )

        try:
            upload_resp = await self.upload_video_to_summarizer(
                camera_name,
                start_time,
                end_time,
                True,
                upload_tag=upload_tag,
                apply_end_buffer=apply_end_buffer,
            )
            if upload_resp["status"] != 200:
                return upload_resp
        except Exception as e:
            logger.error(f"Failed to upload video for embedding search: {e}")
            raise

        url = f"{self.vss_search_url}/manager/videos/search-embeddings/{upload_resp['message']}"
        logger.info(f"Calling search-embeddings API: {url}")

        try:
            response = await asyncio.to_thread(requests.post, url,timeout=30)
            response.raise_for_status()
            message = response.json().get("message", "No message in response.")
            logger.info(f"Embedding search response: {message}")
            return {
                "status": 200,
                "video_id": upload_resp["message"],
                "message": message,
            }
        except requests.RequestException as e:
            logger.error(f"Search embeddings API failed: {e}")
            raise
