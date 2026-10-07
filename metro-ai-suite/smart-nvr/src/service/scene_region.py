# SPDX-FileCopyrightText: (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

from typing import Dict, List, Optional
import logging
import time

import requests
from config import (
    SCENESCAPE_API_BASE_URL,
    SCENESCAPE_API_VERIFY_SSL,
    SCENESCAPE_API_AUTH_PATH,
    SCENESCAPE_API_USER,
    SCENESCAPE_API_PASSWORD,
    SCENESCAPE_API_TIMEOUT_SEC,
    SCENESCAPE_API_RETRY_COUNT,
    SCENESCAPE_API_RETRY_DELAY_SEC,
)

logger = logging.getLogger(__name__)

_scenescape_token: Optional[str] = None


def _new_direct_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    return session


def _scenescape_login(session: requests.Session) -> Optional[str]:
    if not SCENESCAPE_API_USER or not SCENESCAPE_API_PASSWORD:
        logger.warning(
            "SceneScape credentials are not configured; trying unauthenticated API access."
        )
        return None

    auth_url = f"{SCENESCAPE_API_BASE_URL.rstrip('/')}{SCENESCAPE_API_AUTH_PATH}"
    response = session.post(
        auth_url,
        json={"username": SCENESCAPE_API_USER, "password": SCENESCAPE_API_PASSWORD},
        timeout=SCENESCAPE_API_TIMEOUT_SEC,
        verify=SCENESCAPE_API_VERIFY_SSL,
    )
    response.raise_for_status()

    data = response.json() if response.content else {}
    token = data.get("token") if isinstance(data, dict) else None
    if not token:
        raise ValueError("SceneScape auth succeeded but no token was returned")
    return token


def _scenescape_api_get(path: str, params: Optional[Dict] = None) -> Dict:
    global _scenescape_token

    url = f"{SCENESCAPE_API_BASE_URL.rstrip('/')}{path}"
    session = _new_direct_session()

    headers = {}
    if _scenescape_token:
        headers["Authorization"] = f"Token {_scenescape_token}"

    response = session.get(
        url,
        params=params,
        timeout=SCENESCAPE_API_TIMEOUT_SEC,
        verify=SCENESCAPE_API_VERIFY_SSL,
        headers=headers,
    )

    if response.status_code == 401:
        try:
            _scenescape_token = _scenescape_login(session)
        except Exception as auth_err:
            logger.warning("SceneScape auth failed: %s", auth_err)
            response.raise_for_status()

        retry_headers = {}
        if _scenescape_token:
            retry_headers["Authorization"] = f"Token {_scenescape_token}"
        response = session.get(
            url,
            params=params,
            timeout=SCENESCAPE_API_TIMEOUT_SEC,
            verify=SCENESCAPE_API_VERIFY_SSL,
            headers=retry_headers,
        )

    response.raise_for_status()
    return response.json()


def _fetch_scenescape_scenes_once() -> List[Dict[str, str]]:
    data = _scenescape_api_get("/api/v1/scenes")
    rows = data.get("results", data if isinstance(data, list) else [])
    scenes = []
    if isinstance(rows, list):
        for scene in rows:
            if not isinstance(scene, dict):
                continue
            scene_id = scene.get("uid") or scene.get("id") or scene.get("pk")
            scene_name = scene.get("name") or scene_id
            if scene_id:
                scenes.append({"id": str(scene_id), "name": str(scene_name)})
    return scenes


def _fetch_scenescape_regions_once(scene_id: Optional[str] = None) -> List[Dict[str, str]]:
    data = _scenescape_api_get("/api/v1/regions")
    rows = data.get("results", data if isinstance(data, list) else [])
    regions = []
    if isinstance(rows, list):
        for region in rows:
            if not isinstance(region, dict):
                continue

            region_id = region.get("uuid") or region.get("uid") or region.get("id")
            region_name = region.get("name") or region.get("title") or region_id

            scene_ref = region.get("scene")
            if isinstance(scene_ref, dict):
                reg_scene_id = (
                    scene_ref.get("uid") or scene_ref.get("id") or scene_ref.get("pk")
                )
                reg_scene_name = scene_ref.get("name") or reg_scene_id
            else:
                reg_scene_id = scene_ref
                reg_scene_name = region.get("scene_name") or reg_scene_id

            if not region_id:
                continue

            if scene_id and str(reg_scene_id) != str(scene_id):
                continue

            regions.append(
                {
                    "region_id": str(region_id),
                    "region_name": str(region_name),
                    "scene_id": str(reg_scene_id) if reg_scene_id else "",
                    "scene_name": str(reg_scene_name) if reg_scene_name else "",
                }
            )

    return regions


def _retry_scenescape_fetch(fetch_fn):
    for attempt in range(SCENESCAPE_API_RETRY_COUNT):
        try:
            rows = fetch_fn()
            if rows:
                return rows
        except Exception as err:
            logger.warning("SceneScape fetch attempt %s failed: %s", attempt + 1, err)

        if attempt < SCENESCAPE_API_RETRY_COUNT - 1:
            time.sleep(SCENESCAPE_API_RETRY_DELAY_SEC)
    return []


def fetch_scenes_with_retry() -> List[Dict[str, str]]:
    return _retry_scenescape_fetch(_fetch_scenescape_scenes_once)


def fetch_regions_with_retry(scene_id: Optional[str] = None) -> List[Dict[str, str]]:
    return _retry_scenescape_fetch(
        lambda: _fetch_scenescape_regions_once(scene_id=scene_id)
    )
