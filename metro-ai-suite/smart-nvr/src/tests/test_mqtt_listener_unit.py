"""Unit tests for isolated helpers in mqtt_listener to boost coverage without real broker."""
import pytest
from unittest.mock import AsyncMock, patch
from service import mqtt_listener

def test_iso_to_frigate_timestamp_valid():
    ts = mqtt_listener.iso_to_frigate_timestamp("2025-01-02T03:04:05Z")
    assert ts.startswith("17")  # epoch prefix (rough sanity)
    assert "." in ts

def test_iso_to_frigate_timestamp_invalid():
    raw = "not-a-timestamp"
    out = mqtt_listener.iso_to_frigate_timestamp(raw)
    assert out == raw  # falls back unchanged


def test_derive_region_event_types_roi_and_zone_violation():
    payload = {
        "entered": 1,
        "restricted_zone": True,
        "objects": {
            "pedestrian": [{"id": "p1"}],
            "vehicle": [{"id": "v1"}]
        },
    }
    derived = mqtt_listener._derive_region_event_types(payload, num_vehicles=1, num_pedestrians=1)
    assert "roi_entry_exit" in derived
    assert "zone_violation" in derived


def test_derive_region_event_types_near_miss():
    payload = {
        "min_distance": 1.5,
        "relative_velocity": 2.2,
        "time_to_collision": 2.0,
    }
    derived = mqtt_listener._derive_region_event_types(payload, num_vehicles=1, num_pedestrians=1)
    assert "near_miss" in derived


def test_derive_region_event_types_zone_violation_total_objects_threshold():
    payload = {
        "restricted_zone": True,
    }

    # Total objects > 1 should trigger zone violation even without pedestrians.
    derived = mqtt_listener._derive_region_event_types(payload, num_vehicles=2, num_pedestrians=0)
    assert "zone_violation" in derived

    # Total objects <= 1 should not trigger zone violation.
    derived_single = mqtt_listener._derive_region_event_types(payload, num_vehicles=1, num_pedestrians=0)
    assert "zone_violation" not in derived_single


def test_derive_region_event_types_zone_violation_from_topic_count():
    payload = {}
    derived = mqtt_listener._derive_region_event_types(
        payload,
        num_vehicles=2,
        num_pedestrians=0,
        topic_event_type="count",
    )
    assert "zone_violation" in derived


def test_derive_region_event_types_zone_violation_from_topic_objects_presence():
    payload = {}
    derived = mqtt_listener._derive_region_event_types(
        payload,
        num_vehicles=1,
        num_pedestrians=0,
        topic_event_type="objects",
    )
    assert "zone_violation" in derived


def test_extract_topic_event_type_from_region_topic():
    topic = "scenescape/event/region/scene-001/region-001/count"
    assert mqtt_listener._extract_topic_event_type(topic) == "count"


def test_extract_counts_supports_counts_and_objects_list_shape():
    payload = {
        "counts": {"vehicle": 2, "pedestrian": 1},
        "objects": [
            {"type": "vehicle"},
            {"category": "vehicle"},
            {"type": "pedestrian"},
        ],
    }
    vehicles, pedestrians = mqtt_listener._extract_counts(payload)
    assert vehicles == 2
    assert pedestrians == 1


def test_derive_region_event_types_with_entered_exited_arrays():
    payload = {
        "entered": [{"id": "v1"}],
        "exited": [],
    }
    derived = mqtt_listener._derive_region_event_types(payload, num_vehicles=1, num_pedestrians=0)
    assert "roi_entry_exit" in derived


@pytest.mark.asyncio
async def test_handle_scenescape_message_region_topic_dispatches_derived_events():
    payload = {
        "timestamp": "2026-10-05T10:00:00Z",
        "scene_id": "scene-001",
        "region_id": "region-001",
        "region_name": "WCWLK",
        "entered": 1,
        "restricted_zone": True,
        "objects": {
            "pedestrian": [{"id": "p1"}],
            "vehicle": [{"id": "v1"}],
        },
        "min_distance": 1.2,
        "relative_velocity": 2.0,
        "time_to_collision": 1.8,
    }
    topic = "scenescape/event/region/scene-001/region-001/count"

    with patch("service.mqtt_listener.process_event", AsyncMock(return_value={"ok": True})) as mock_process:
        await mqtt_listener.handle_scenescape_message(payload, topic, state={"last_processed": 0, "interval": 0}, broker_id="si1")

    labels = [call.args[0].get("label") for call in mock_process.await_args_list]
    assert "roi_entry_exit" in labels
    assert "zone_violation" in labels
    assert "near_miss" in labels


@pytest.mark.asyncio
async def test_handle_scenescape_message_region_topic_with_counts_shape_dispatches_zone_violation():
    payload = {
        "timestamp": "2026-10-06T09:10:00Z",
        "scene_id": "scene-001",
        "scene_name": "Intersection-Demo",
        "region_id": "region-001",
        "region_name": "WCWLK",
        "counts": {"pedestrian": 0, "vehicle": 2},
        "objects": [{"type": "vehicle"}],
        "entered": [{"id": "v1"}],
        "exited": [],
    }
    topic = "scenescape/event/region/scene-001/region-001/count"

    with patch("service.mqtt_listener.process_event", AsyncMock(return_value={"ok": True})) as mock_process:
        await mqtt_listener.handle_scenescape_message(payload, topic, state={"last_processed": 0, "interval": 0}, broker_id="si1")

    labels = [call.args[0].get("label") for call in mock_process.await_args_list]
    assert "zone_violation" in labels


@pytest.mark.asyncio
async def test_handle_scenescape_message_region_topic_objects_type_dispatches_zone_violation():
    payload = {
        "timestamp": "2026-10-06T09:10:00Z",
        "scene_id": "scene-001",
        "region_id": "region-001",
        "region_name": "NOPED",
        "counts": {"pedestrian": 0, "vehicle": 1},
        "objects": [{"type": "vehicle"}],
        "entered": [{"id": "v1"}],
        "exited": [],
    }
    topic = "scenescape/event/region/scene-001/region-001/objects"

    with patch("service.mqtt_listener.process_event", AsyncMock(return_value={"ok": True})) as mock_process:
        await mqtt_listener.handle_scenescape_message(payload, topic, state={"last_processed": 0, "interval": 0}, broker_id="si1")

    labels = [call.args[0].get("label") for call in mock_process.await_args_list]
    assert "zone_violation" in labels
