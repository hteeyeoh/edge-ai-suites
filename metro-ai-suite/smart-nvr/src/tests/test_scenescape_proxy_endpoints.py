# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0


def test_get_scenescape_scenes_proxy(client, monkeypatch):
    import api.router as router_module

    expected = [{"id": "scene-1", "name": "Intersection-Demo"}]

    monkeypatch.setattr(
        router_module,
        "fetch_scenes_with_retry",
        lambda: expected,
    )

    resp = client.get("/scenes")
    assert resp.status_code == 200
    assert resp.json() == expected


def test_get_scenescape_regions_proxy_passes_scene_id(client, monkeypatch):
    import api.router as router_module

    captured = {"scene_id": None}

    def fake_fetch_regions_with_retry(scene_id=None):
        captured["scene_id"] = scene_id
        return [
            {
                "region_id": "region-1",
                "region_name": "WCWLK",
                "scene_id": scene_id or "",
                "scene_name": "Intersection-Demo",
            }
        ]

    monkeypatch.setattr(
        router_module,
        "fetch_regions_with_retry",
        fake_fetch_regions_with_retry,
    )

    resp = client.get("/regions", params={"scene_id": "scene-1"})
    assert resp.status_code == 200
    assert captured["scene_id"] == "scene-1"
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["region_id"] == "region-1"
