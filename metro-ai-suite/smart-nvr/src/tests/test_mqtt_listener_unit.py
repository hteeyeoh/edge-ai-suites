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


def test_derive_region_labels_activity_yields_region_event():
    payload = {
        "entered": 1,
        "restricted_zone": True,
        "objects": {
            "pedestrian": [{"id": "p1"}],
            "vehicle": [{"id": "v1"}]
        },
    }
    derived = mqtt_listener._derive_region_labels(payload, num_vehicles=1, num_pedestrians=1)
    assert "region_event" in derived


def test_derive_region_labels_participants_yield_region_event():
    payload = {
        "participants": [{"id": "p1"}],
    }
    derived = mqtt_listener._derive_region_labels(payload, num_vehicles=1, num_pedestrians=1)
    assert "region_event" in derived


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


def test_derive_region_labels_with_entered_exited_arrays():
    payload = {
        "entered": [{"id": "v1"}],
        "exited": [],
    }
    derived = mqtt_listener._derive_region_labels(payload, num_vehicles=1, num_pedestrians=0)
    assert "region_event" in derived


@pytest.mark.asyncio
async def test_handle_scenescape_message_region_topic_dispatches_region_event():
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
        "participants": [{"id": "p1"}],
        "visibility": ["camera1"],
    }
    topic = "scenescape/event/region/scene-001/region-001/count"

    with patch("service.mqtt_listener.process_event", AsyncMock(return_value={"ok": True})) as mock_process:
        await mqtt_listener.handle_scenescape_message(payload, topic, state={"last_processed": 0, "interval": 0}, broker_id="si1")

    labels = [call.args[0].get("label") for call in mock_process.await_args_list]
    assert "region_event" in labels


@pytest.mark.asyncio
async def test_handle_scenescape_message_region_topic_with_counts_shape_derives_region_event():
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
        "visibility": ["camera1"],
    }
    topic = "scenescape/event/region/scene-001/region-001/count"

    with patch("service.mqtt_listener.process_event", AsyncMock(return_value={"ok": True})) as mock_process:
        await mqtt_listener.handle_scenescape_message(payload, topic, state={"last_processed": 0, "interval": 0}, broker_id="si1")

    labels = [call.args[0].get("label") for call in mock_process.await_args_list]
    assert "region_event" in labels


@pytest.mark.asyncio
async def test_handle_scenescape_message_region_topic_objects_type_derives_region_event():
    payload = {
        "timestamp": "2026-10-06T09:10:00Z",
        "scene_id": "scene-001",
        "region_id": "region-001",
        "region_name": "NOPED",
        "counts": {"pedestrian": 0, "vehicle": 1},
        "objects": [{"type": "vehicle"}],
        "entered": [{"id": "v1"}],
        "exited": [],
        "visibility": ["camera1"],
    }
    topic = "scenescape/event/region/scene-001/region-001/objects"

    with patch("service.mqtt_listener.process_event", AsyncMock(return_value={"ok": True})) as mock_process:
        await mqtt_listener.handle_scenescape_message(payload, topic, state={"last_processed": 0, "interval": 0}, broker_id="si1")

    labels = [call.args[0].get("label") for call in mock_process.await_args_list]
    assert "region_event" in labels


@pytest.mark.asyncio
async def test_region_topic_camera_uses_nested_visibility_value():
    payload = {
        "timestamp": "2026-10-07T07:48:48.279Z",
        "scene_id": "97781c36-b53a-4749-87e6-8815da99bac7",
        "scene_name": "Intersection-Demo",
        "region_id": "de559bea-4c3a-4f21-9b06-2543db159df6",
        "region_name": "SCWLK",
        "counts": {"vehicle": 0, "pedestrian": 0},
        "objects": [],
        "entered": [],
        "exited": [
            {
                "object": {
                    "category": "vehicle",
                    "visibility": ["camera1"],
                },
                "dwell": 0.59,
            }
        ],
    }
    topic = "scenescape/event/region/97781c36-b53a-4749-87e6-8815da99bac7/de559bea-4c3a-4f21-9b06-2543db159df6/objects"

    with patch("service.mqtt_listener.process_event", AsyncMock(return_value={"ok": True})) as mock_process:
        await mqtt_listener.handle_scenescape_message(
            payload,
            topic,
            state={"last_processed": 0, "interval": 0},
            broker_id="si1",
        )

    assert mock_process.await_count == 1
    event_data = mock_process.await_args_list[0].args[0]
    assert event_data["camera"] == "si1-camera1"


@pytest.mark.asyncio
async def test_region_topic_clip_window_uses_latest_object_entered_time():
    payload = {
        "timestamp": "2026-10-08T01:52:41.673Z",
        "scene_id": "97781c36-b53a-4749-87e6-8815da99bac7",
        "scene_name": "Intersection-Demo",
        "region_id": "58172fbc-d9a2-4a50-a289-ec1032f7be49",
        "region_name": "EBLANE",
        "counts": {"vehicle": 3},
        "objects": [
            {
                "type": "vehicle",
                "visibility": ["camera2"],
                "regions": {
                    "58172fbc-d9a2-4a50-a289-ec1032f7be49": {
                        "entered": "2026-10-08T01:52:24.128Z",
                        "dwell": 17.545,
                    }
                },
            },
            {
                "type": "vehicle",
                "visibility": ["camera2"],
                "regions": {
                    "58172fbc-d9a2-4a50-a289-ec1032f7be49": {
                        "entered": "2026-10-08T01:52:41.488Z",
                        "dwell": 0.185,
                    }
                },
            },
        ],
        "entered": [{"id": "new-vehicle"}],
        "exited": [],
    }
    topic = "scenescape/event/region/97781c36-b53a-4749-87e6-8815da99bac7/58172fbc-d9a2-4a50-a289-ec1032f7be49/objects"

    with patch("service.mqtt_listener.process_event", AsyncMock(return_value={"ok": True})) as mock_process:
        await mqtt_listener.handle_scenescape_message(
            payload,
            topic,
            state={"last_processed": 0, "interval": 0},
            broker_id="si1",
        )

    assert mock_process.await_count == 1
    event_data = mock_process.await_args_list[0].args[0]
    latest_entered_epoch = float(mqtt_listener.iso_to_frigate_timestamp("2026-10-08T01:52:41.488Z"))
    assert event_data["start_time"] == pytest.approx(
        latest_entered_epoch - mqtt_listener.REGION_CLIP_PRE_ROLL_SECONDS, abs=1e-6
    )
    assert event_data["end_time"] == pytest.approx(
        latest_entered_epoch + mqtt_listener.REGION_CLIP_POST_ROLL_SECONDS, abs=1e-6
    )


@pytest.mark.asyncio
async def test_region_topic_emits_event_per_visibility_camera():
    payload = {
        "timestamp": "2026-10-08T04:04:47.024Z",
        "scene_id": "97781c36-b53a-4749-87e6-8815da99bac7",
        "region_id": "e9f0981d-8535-4782-8e85-a04cb2605db5",
        "region_name": "NOPED",
        "counts": {"vehicle": 4, "pedestrian": 0},
        "objects": [
            {
                "type": "vehicle",
                "visibility": ["camera2"],
                "regions": {
                    "e9f0981d-8535-4782-8e85-a04cb2605db5": {
                        "entered": "2026-10-08T04:04:46.894Z",
                        "dwell": 0.129,
                    }
                },
            },
            {
                "type": "vehicle",
                "visibility": ["camera3"],
                "regions": {
                    "e9f0981d-8535-4782-8e85-a04cb2605db5": {
                        "entered": "2026-10-08T04:04:44.561Z",
                        "dwell": 2.463,
                    }
                },
            },
            {
                "type": "vehicle",
                "visibility": ["camera1", "camera2"],
                "regions": {
                    "e9f0981d-8535-4782-8e85-a04cb2605db5": {
                        "entered": "2026-10-08T04:04:46.034Z",
                        "dwell": 0.99,
                    }
                },
            },
        ],
        "entered": [{"id": "new-vehicle"}],
    }
    topic = "scenescape/event/region/97781c36-b53a-4749-87e6-8815da99bac7/e9f0981d-8535-4782-8e85-a04cb2605db5/objects"

    with patch("service.mqtt_listener.process_event", AsyncMock(return_value={"ok": True})) as mock_process:
        await mqtt_listener.handle_scenescape_message(
            payload,
            topic,
            state={"last_processed": 0, "interval": 0},
            broker_id="si1-region",
        )

    emitted_cameras = [call.args[0]["camera"] for call in mock_process.await_args_list]
    assert emitted_cameras == [
        "si1-region-camera2",
        "si1-region-camera3",
        "si1-region-camera1",
    ]
