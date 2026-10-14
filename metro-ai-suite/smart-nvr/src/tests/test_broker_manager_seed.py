# SPDX-FileCopyrightText: (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import pytest
import yaml

from service import broker_manager


def _patch_stateful_redis(monkeypatch, initial_brokers=None):
    """Stateful fake redis_store so get_brokers reflects prior save_broker calls."""
    store = {b["id"]: b for b in (initial_brokers or [])}

    async def fake_get_brokers(_request=None):
        return list(store.values())

    async def fake_save_broker(broker_id, broker_data, _request=None):
        store[broker_id] = {"id": broker_id, **broker_data}

    async def fake_delete_broker(broker_id, _request=None):
        return store.pop(broker_id, None) is not None

    async def fake_sync_yaml_from_redis(request=None, path=None):
        return None

    monkeypatch.setattr(broker_manager.redis_store, "get_brokers", fake_get_brokers)
    monkeypatch.setattr(broker_manager.redis_store, "save_broker", fake_save_broker)
    monkeypatch.setattr(broker_manager.redis_store, "delete_broker", fake_delete_broker)
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
    """When no YAML entries and no existing SceneScape brokers, seed a single
    si1 broker covering both default topics."""

    store = _patch_stateful_redis(monkeypatch)
    _patch_scenescape_env(monkeypatch)

    path = tmp_path / "brokers.yaml"
    path.write_text("brokers: []\n")

    await broker_manager.load_yaml_brokers(path=str(path))

    assert "si1" in store
    assert "si1-region" not in store
    assert store["si1"]["camera_topic"] == "scenescape/data/camera/#"
    assert store["si1"]["region_topic"] == "scenescape/event/region/+/+/+"


@pytest.mark.asyncio
async def test_load_yaml_brokers_seeds_region_when_yaml_has_camera_only(monkeypatch, tmp_path):
    """Regression: YAML pre-seeded with a camera-topic si1 (e.g. by setup.sh's
    reset_scenescape_brokers_to_local) must get the region topic merged in,
    not spawn a separate broker connection."""

    store = _patch_stateful_redis(monkeypatch)
    _patch_scenescape_env(monkeypatch)

    path = tmp_path / "brokers.yaml"
    path.write_text(
        "brokers:\n"
        "- id: si1\n"
        "  name: SI Node 1\n"
        "  host: 10.223.23.28\n"
        "  port: 1883\n"
        "  camera_topic: scenescape/data/camera/#\n"
        "  type: scenescape\n"
        "  use_tls: true\n"
    )

    await broker_manager.load_yaml_brokers(path=str(path))

    assert "si1-region" not in store
    assert store["si1"]["region_topic"] == "scenescape/event/region/+/+/+"
    # Pre-existing YAML camera entry is left untouched, not duplicated/overwritten.
    assert store["si1"]["camera_topic"] == "scenescape/data/camera/#"


def test_reason_code_to_int_handles_common_shapes():
    class WithValue:
        value = 128

    class NotConvertible:
        def __int__(self):
            raise TypeError("nope")

    assert broker_manager._reason_code_to_int(1) == 1
    assert broker_manager._reason_code_to_int(WithValue()) == 128
    assert broker_manager._reason_code_to_int(NotConvertible()) == -1


@pytest.mark.asyncio
async def test_load_yaml_brokers_non_scenescape_skips_seed_but_syncs(monkeypatch, tmp_path):
    """When scenescape mode is off, broker seeding logic is skipped and YAML sync still runs."""
    called = {}

    async def fake_get_brokers(_request=None):
        return []

    async def fake_save_broker(_broker_id, _broker_data, _request=None):
        raise AssertionError("save_broker should not be called when scenescape mode is off")

    async def fake_sync_yaml_from_redis(request=None, path=None):
        called["path"] = path

    monkeypatch.setattr(broker_manager, "NVR_SCENESCAPE_ENABLED", False)
    monkeypatch.setattr(broker_manager.redis_store, "get_brokers", fake_get_brokers)
    monkeypatch.setattr(broker_manager.redis_store, "save_broker", fake_save_broker)
    monkeypatch.setattr(broker_manager, "sync_yaml_from_redis", fake_sync_yaml_from_redis)

    path = tmp_path / "missing.yaml"
    await broker_manager.load_yaml_brokers(path=str(path))

    assert called["path"] == str(path)


@pytest.mark.asyncio
async def test_load_yaml_brokers_with_full_topic_coverage_does_not_seed(monkeypatch, tmp_path):
    """If both topics are already covered, the scenescape seed/merge branch is skipped."""
    _patch_scenescape_env(monkeypatch)
    save_calls = []

    async def fake_get_brokers(_request=None):
        return [
            {
                "id": "si1",
                "name": "SI Node 1",
                "host": "10.1.1.1",
                "port": 1883,
                "camera_topic": "scenescape/data/camera/#",
                "region_topic": "scenescape/event/region/+/+/+",
                "type": "scenescape",
                "use_tls": True,
            }
        ]

    async def fake_save_broker(broker_id, _broker_data, _request=None):
        save_calls.append(broker_id)

    async def fake_sync_yaml_from_redis(request=None, path=None):
        return None

    monkeypatch.setattr(broker_manager.redis_store, "get_brokers", fake_get_brokers)
    monkeypatch.setattr(broker_manager.redis_store, "save_broker", fake_save_broker)
    monkeypatch.setattr(broker_manager, "sync_yaml_from_redis", fake_sync_yaml_from_redis)

    path = tmp_path / "brokers.yaml"
    path.write_text("brokers: []\n")
    await broker_manager.load_yaml_brokers(path=str(path))

    assert save_calls == []


@pytest.mark.asyncio
async def test_load_yaml_brokers_existing_si1_adds_camera_topic_only(monkeypatch, tmp_path):
    """If only region topic is present, merge in camera topic without changing region topic."""
    store = _patch_stateful_redis(
        monkeypatch,
        initial_brokers=[
            {
                "id": "si1",
                "name": "SI Node 1",
                "host": "10.223.23.28",
                "port": 1883,
                "region_topic": "scenescape/event/region/+/+/+",
                "type": "scenescape",
                "use_tls": True,
            }
        ],
    )
    _patch_scenescape_env(monkeypatch)

    path = tmp_path / "brokers.yaml"
    path.write_text("brokers: []\n")
    await broker_manager.load_yaml_brokers(path=str(path))

    assert store["si1"]["camera_topic"] == "scenescape/data/camera/#"
    assert store["si1"]["region_topic"] == "scenescape/event/region/+/+/+"


@pytest.mark.asyncio
async def test_sync_yaml_from_redis_writes_header_and_brokers(monkeypatch, tmp_path):
    """sync_yaml_from_redis persists header and broker list to YAML file."""

    async def fake_get_brokers(_request=None):
        return [
            {
                "id": "si1",
                "name": "Smart Intersection 1",
                "host": "10.1.1.1",
                "port": 1883,
                "camera_topic": "scenescape/data/camera/#",
                "region_topic": "scenescape/event/region/+/+/+",
                "type": "scenescape",
                "use_tls": True,
            }
        ]

    monkeypatch.setattr(broker_manager.redis_store, "get_brokers", fake_get_brokers)

    path = tmp_path / "out.yaml"
    await broker_manager.sync_yaml_from_redis(path=str(path))

    text = path.read_text()
    assert "Scenescape-mode MQTT brokers" in text
    assert "brokers:" in text

    parsed = yaml.safe_load(path.read_text())
    assert parsed["brokers"][0]["id"] == "si1"


@pytest.mark.asyncio
async def test_sync_yaml_from_redis_handles_oserror(monkeypatch):
    """sync_yaml_from_redis should swallow OSError and not raise."""

    async def fake_get_brokers(_request=None):
        return [{"id": "si1"}]

    monkeypatch.setattr(broker_manager.redis_store, "get_brokers", fake_get_brokers)

    class OpenFailure:
        def __call__(self, *_args, **_kwargs):
            raise OSError("disk full")

    monkeypatch.setattr("builtins.open", OpenFailure())

    await broker_manager.sync_yaml_from_redis(path="/tmp/should-not-write.yaml")
