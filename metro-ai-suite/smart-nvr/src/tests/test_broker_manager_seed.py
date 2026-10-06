# SPDX-FileCopyrightText: (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import pytest

from service import broker_manager


@pytest.mark.asyncio
async def test_load_yaml_brokers_seeds_camera_and_region(monkeypatch, tmp_path):
    """When no YAML entries and no existing SceneScape brokers, seed both defaults."""

    calls = []

    async def fake_get_brokers(_request=None):
        return []

    async def fake_save_broker(broker_id, broker_data, _request=None):
        calls.append((broker_id, broker_data))

    async def fake_sync_yaml_from_redis(request=None, path=None):
        return None

    monkeypatch.setattr(broker_manager.redis_store, "get_brokers", fake_get_brokers)
    monkeypatch.setattr(broker_manager.redis_store, "save_broker", fake_save_broker)
    monkeypatch.setattr(broker_manager, "sync_yaml_from_redis", fake_sync_yaml_from_redis)

    monkeypatch.setattr(broker_manager, "NVR_SCENESCAPE_ENABLED", True)
    monkeypatch.setattr(broker_manager, "SCENESCAPE_MQTT_BROKER", "broker.scenescape.intel.com")
    monkeypatch.setattr(broker_manager, "SCENESCAPE_MQTT_PORT", 1883)
    monkeypatch.setattr(broker_manager, "SCENESCAPE_MQTT_TOPIC", "scenescape/data/camera/#")
    monkeypatch.setattr(
        broker_manager,
        "SCENESCAPE_REGION_MQTT_TOPIC",
        "scenescape/event/region/+/+/+",
    )

    path = tmp_path / "brokers.yaml"
    path.write_text("brokers: []\n")

    await broker_manager.load_yaml_brokers(path=str(path))

    saved = {broker_id: payload for broker_id, payload in calls}
    assert "si1" in saved
    assert "si1-region" in saved
    assert saved["si1"]["topic"] == "scenescape/data/camera/#"
    assert saved["si1-region"]["topic"] == "scenescape/event/region/+/+/+"


def test_reason_code_to_int_handles_common_shapes():
    class WithValue:
        value = 128

    class NotConvertible:
        def __int__(self):
            raise TypeError("nope")

    assert broker_manager._reason_code_to_int(1) == 1
    assert broker_manager._reason_code_to_int(WithValue()) == 128
    assert broker_manager._reason_code_to_int(NotConvertible()) == -1
