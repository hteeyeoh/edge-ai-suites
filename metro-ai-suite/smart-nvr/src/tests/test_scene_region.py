# SPDX-FileCopyrightText: (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import requests

from service import scene_region as sr


class DummyResponse:
    def __init__(self, status_code=200, json_data=None, content=b"1"):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self.content = content

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class DummySession:
    def __init__(self, get_responses=None, post_response=None):
        self.get_responses = list(get_responses or [])
        self.post_response = post_response or DummyResponse()
        self.get_calls = []
        self.post_calls = []

    def get(self, url, params=None, timeout=None, verify=None, headers=None):
        self.get_calls.append(
            {
                "url": url,
                "params": params,
                "timeout": timeout,
                "verify": verify,
                "headers": headers or {},
            }
        )
        return self.get_responses.pop(0)

    def post(self, url, json=None, timeout=None, verify=None):
        self.post_calls.append(
            {
                "url": url,
                "json": json,
                "timeout": timeout,
                "verify": verify,
            }
        )
        return self.post_response


def test_new_direct_session_disables_env_proxy():
    session = sr._new_direct_session()
    assert session.trust_env is False


def test_scenescape_login_without_credentials_returns_none(monkeypatch):
    monkeypatch.setattr(sr, "SCENESCAPE_API_USER", "")
    monkeypatch.setattr(sr, "SCENESCAPE_API_PASSWORD", "")

    token = sr._scenescape_login(DummySession())

    assert token is None


def test_scenescape_login_success_returns_token(monkeypatch):
    monkeypatch.setattr(sr, "SCENESCAPE_API_USER", "user")
    monkeypatch.setattr(sr, "SCENESCAPE_API_PASSWORD", "pass")
    monkeypatch.setattr(sr, "SCENESCAPE_API_BASE_URL", "http://scene")
    monkeypatch.setattr(sr, "SCENESCAPE_API_AUTH_PATH", "/auth")

    session = DummySession(post_response=DummyResponse(json_data={"token": "abc123"}))
    token = sr._scenescape_login(session)

    assert token == "abc123"
    assert session.post_calls[0]["url"] == "http://scene/auth"


def test_scenescape_login_missing_token_raises(monkeypatch):
    monkeypatch.setattr(sr, "SCENESCAPE_API_USER", "user")
    monkeypatch.setattr(sr, "SCENESCAPE_API_PASSWORD", "pass")

    session = DummySession(post_response=DummyResponse(json_data={}))

    try:
        sr._scenescape_login(session)
        assert False, "Expected ValueError when token is absent"
    except ValueError as err:
        assert "no token" in str(err).lower()


def test_scenescape_api_get_uses_existing_token_header(monkeypatch):
    session = DummySession(get_responses=[DummyResponse(json_data={"ok": True})])
    monkeypatch.setattr(sr, "_new_direct_session", lambda: session)
    monkeypatch.setattr(sr, "SCENESCAPE_API_BASE_URL", "http://scene")
    sr._scenescape_token = "cached-token"

    data = sr._scenescape_api_get("/api/v1/scenes", params={"k": "v"})

    assert data == {"ok": True}
    assert session.get_calls[0]["headers"]["Authorization"] == "Token cached-token"


def test_scenescape_api_get_retries_after_401_and_login(monkeypatch):
    session = DummySession(
        get_responses=[
            DummyResponse(status_code=401, json_data={"detail": "unauthorized"}),
            DummyResponse(status_code=200, json_data={"results": []}),
        ]
    )
    monkeypatch.setattr(sr, "_new_direct_session", lambda: session)
    monkeypatch.setattr(sr, "_scenescape_login", lambda _session: "fresh-token")
    monkeypatch.setattr(sr, "SCENESCAPE_API_BASE_URL", "http://scene")
    sr._scenescape_token = None

    data = sr._scenescape_api_get("/api/v1/regions")

    assert data == {"results": []}
    assert len(session.get_calls) == 2
    assert session.get_calls[1]["headers"]["Authorization"] == "Token fresh-token"


def test_scenescape_api_get_401_and_failed_login_raises(monkeypatch):
    session = DummySession(get_responses=[DummyResponse(status_code=401)])
    monkeypatch.setattr(sr, "_new_direct_session", lambda: session)
    monkeypatch.setattr(
        sr,
        "_scenescape_login",
        lambda _session: (_ for _ in ()).throw(RuntimeError("bad creds")),
    )
    monkeypatch.setattr(sr, "SCENESCAPE_API_BASE_URL", "http://scene")

    try:
        sr._scenescape_api_get("/api/v1/scenes")
        assert False, "Expected HTTPError from original 401 response"
    except requests.HTTPError:
        pass


def test_scenescape_api_get_401_retry_without_token_header(monkeypatch):
    session = DummySession(
        get_responses=[
            DummyResponse(status_code=401, json_data={"detail": "unauthorized"}),
            DummyResponse(status_code=200, json_data={"ok": True}),
        ]
    )
    monkeypatch.setattr(sr, "_new_direct_session", lambda: session)
    monkeypatch.setattr(sr, "_scenescape_login", lambda _session: None)
    monkeypatch.setattr(sr, "SCENESCAPE_API_BASE_URL", "http://scene")
    sr._scenescape_token = None

    data = sr._scenescape_api_get("/api/v1/scenes")

    assert data == {"ok": True}
    assert session.get_calls[1]["headers"] == {}


def test_fetch_scenescape_scenes_once_parses_results(monkeypatch):
    monkeypatch.setattr(
        sr,
        "_scenescape_api_get",
        lambda _path: {
            "results": [
                {"uid": "scene-1", "name": "Main"},
                {"id": "scene-2"},
                {"name": "no-id"},
                "invalid",
            ]
        },
    )

    scenes = sr._fetch_scenescape_scenes_once()

    assert scenes == [
        {"id": "scene-1", "name": "Main"},
        {"id": "scene-2", "name": "scene-2"},
    ]


def test_fetch_scenescape_scenes_once_handles_non_list_rows(monkeypatch):
    monkeypatch.setattr(sr, "_scenescape_api_get", lambda _path: {"results": {"bad": "shape"}})

    assert sr._fetch_scenescape_scenes_once() == []


def test_fetch_scenescape_regions_once_parses_and_filters(monkeypatch):
    monkeypatch.setattr(
        sr,
        "_scenescape_api_get",
        lambda _path: {
            "results": [
                "invalid",
                {
                    "uuid": "r1",
                    "name": "Crosswalk",
                    "scene": {"uid": "s1", "name": "Scene One"},
                },
                {
                    "id": "r2",
                    "title": "Road",
                    "scene": "s2",
                    "scene_name": "Scene Two",
                },
                {"name": "missing-id"},
            ]
        },
    )

    all_regions = sr._fetch_scenescape_regions_once()
    only_s1 = sr._fetch_scenescape_regions_once(scene_id="s1")

    assert len(all_regions) == 2
    assert all_regions[0]["region_id"] == "r1"
    assert all_regions[1]["scene_id"] == "s2"
    assert only_s1 == [
        {
            "region_id": "r1",
            "region_name": "Crosswalk",
            "scene_id": "s1",
            "scene_name": "Scene One",
        }
    ]


def test_fetch_scenescape_regions_once_handles_non_list_rows(monkeypatch):
    monkeypatch.setattr(sr, "_scenescape_api_get", lambda _path: {"results": "not-a-list"})

    assert sr._fetch_scenescape_regions_once() == []


def test_retry_fetch_retries_and_sleeps(monkeypatch):
    monkeypatch.setattr(sr, "SCENESCAPE_API_RETRY_COUNT", 3)
    monkeypatch.setattr(sr, "SCENESCAPE_API_RETRY_DELAY_SEC", 0)

    sleep_calls = []
    monkeypatch.setattr(sr.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    state = {"count": 0}

    def flaky_fetch():
        state["count"] += 1
        if state["count"] == 1:
            raise RuntimeError("temporary")
        return [{"ok": True}]

    rows = sr._retry_scenescape_fetch(flaky_fetch)

    assert rows == [{"ok": True}]
    assert sleep_calls == [0]


def test_retry_fetch_returns_empty_after_exhaustion(monkeypatch):
    monkeypatch.setattr(sr, "SCENESCAPE_API_RETRY_COUNT", 2)
    monkeypatch.setattr(sr, "SCENESCAPE_API_RETRY_DELAY_SEC", 0)

    sleep_calls = []
    monkeypatch.setattr(sr.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    rows = sr._retry_scenescape_fetch(lambda: [])

    assert rows == []
    assert sleep_calls == [0]


def test_public_retry_wrappers_delegate_correctly(monkeypatch):
    monkeypatch.setattr(sr, "_fetch_scenescape_scenes_once", lambda: [{"id": "s"}])

    captured = {}

    def fake_fetch_regions(scene_id=None):
        captured["scene_id"] = scene_id
        return [{"region_id": "r"}]

    monkeypatch.setattr(sr, "_fetch_scenescape_regions_once", fake_fetch_regions)

    def passthrough_retry(fetch_fn):
        return fetch_fn()

    monkeypatch.setattr(sr, "_retry_scenescape_fetch", passthrough_retry)

    scenes = sr.fetch_scenes_with_retry()
    regions = sr.fetch_regions_with_retry(scene_id="s-1")

    assert scenes == [{"id": "s"}]
    assert regions == [{"region_id": "r"}]
    assert captured["scene_id"] == "s-1"
