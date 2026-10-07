# SPDX-FileCopyrightText: (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import pytest

from service import broker_manager


def _patch_stateful_redis(monkeypatch, initial_brokers=None):
    """Stateful fake redis_store so get_brokers reflects prior save_broker calls."""
    store = {b["id"]: b for b in (initial_brokers or [])}

    async def fake_get_brokers(_request=None):
        return list(store.values())

    async def fake_save_broker(broker_id, broker_data, _request=None):
        store[broker_id] = {"id": broker_id, **broker_data}

    async def fake_sync_yaml_from_redis(request=None, path=None):
        return None

    monkeypatch.setattr(broker_manager.redis_store, "get_brokers", fake_get_brokers)
    monkeypatch.setattr(broker_manager.redis_store, "save_broker", fake_save_broker)
    monkeypatch.setattr(broker_manager, "sync_yaml_from_redis", fake_sync_yaml_from_redis)
    return store


def _patch_scenescape_env(monkeypatch):
    monkeypatch.setattr(broker_manager, "NVR_SCENESCAPE_ENABLED", True)
    monkeypatch.setattr(broker_manager, "SCENESCAPE_MQTT_BROKER", "broker.scenescape.intel.com")
    monkeypatch.setattr(broker_manager, "SCENESCAPE_MQTT_PORT", 1883)
    monkeypatch.setattr(broker_manager, "SCENESCAPE_MQTT_TOPIC", "scenescape/data/camera/#")
    monkeypatch.setattr(
        broker_manager,
        "SCENESCAPE_REGION_MQTT_TOPIC",
        "scenescape/event/region/+/+/+",
    )


@pytest.mark.asyncio
async def test_load_yaml_brokers_seeds_camera_and_region(monkeypatch, tmp_path):
    """When no YAML entries and no existing SceneScape brokers, seed both defaults."""

    store = _patch_stateful_redis(monkeypatch)
    _patch_scenescape_env(monkeypatch)

    path = tmp_path / "brokers.yaml"
    path.write_text("brokers: []\n")

    await broker_manager.load_yaml_brokers(path=str(path))

    assert "si1" in store
    assert "si1-region" in store
    assert store["si1"]["topic"] == "scenescape/data/camera/#"
    assert store["si1-region"]["topic"] == "scenescape/event/region/+/+/+"


@pytest.mark.asyncio
async def test_load_yaml_brokers_seeds_region_when_yaml_has_camera_only(monkeypatch, tmp_path):
    """Regression: YAML pre-seeded with a camera-topic si1 (e.g. by setup.sh's
    reset_scenescape_brokers_to_local) must not block seeding the region broker."""

    store = _patch_stateful_redis(monkeypatch)
    _patch_scenescape_env(monkeypatch)

    path = tmp_path / "brokers.yaml"
    path.write_text(
        "brokers:\n"
        "- id: si1\n"
        "  name: SI Node 1\n"
        "  host: 10.223.23.28\n"
        "  port: 1883\n"
        "  topic: scenescape/data/camera/#\n"
        "  type: scenescape\n"
        "  use_tls: true\n"
    )

    await broker_manager.load_yaml_brokers(path=str(path))

    assert "si1-region" in store
    assert store["si1-region"]["topic"] == "scenescape/event/region/+/+/+"
    # Pre-existing YAML camera entry is left untouched, not duplicated/overwritten.
    assert store["si1"]["topic"] == "scenescape/data/camera/#"


def test_reason_code_to_int_handles_common_shapes():
    class WithValue:
        value = 128

    class NotConvertible:
        def __int__(self):
            raise TypeError("nope")

    assert broker_manager._reason_code_to_int(1) == 1
    assert broker_manager._reason_code_to_int(WithValue()) == 128
    assert broker_manager._reason_code_to_int(NotConvertible()) == -1
