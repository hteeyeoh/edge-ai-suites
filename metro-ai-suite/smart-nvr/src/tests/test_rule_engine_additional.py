"""Additional tests for rule_engine.process_event to raise coverage.

Exercises matching, non-matching, source mismatch, count thresholds (pedestrian & vehicle),
and successful dispatch + store paths.
"""
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_process_event_rule_match_without_threshold():
    from service import rule_engine
    event = {"label": "car", "camera": "garage"}
    rules = [
        {"id": "r1", "label": "car", "camera": "garage", "action": "summarize"},
        {"id": "r2", "label": "person", "camera": "garage", "action": "summarize"},  # no match
    ]

    with patch("service.rule_engine.get_rules", AsyncMock(return_value=rules)), \
         patch("service.rule_engine.dispatch_action", AsyncMock(return_value={"ok": True})) as dispatch, \
         patch("service.rule_engine.store_response", AsyncMock()) as store:
        await rule_engine.process_event(event, context={"source": None})
        dispatch.assert_awaited_once()
        store.assert_awaited_once()
        # event mutated with rule_id before dispatch
        assert event.get("rule_id") == "r1"

@pytest.mark.asyncio
async def test_process_event_source_mismatch_skips():
    from service import rule_engine
    event = {"label": "car", "camera": "garage"}
    rules = [
        {"id": "r1", "label": "car", "camera": "garage", "action": "summarize", "source": "frigate"},
    ]
    with patch("service.rule_engine.get_rules", AsyncMock(return_value=rules)), \
         patch("service.rule_engine.dispatch_action", AsyncMock()) as dispatch:
        await rule_engine.process_event(event, context={"source": "scenescape"})
        dispatch.assert_not_called()
        assert "rule_id" not in event

@pytest.mark.asyncio
async def test_process_event_threshold_vehicle_pass_and_fail():
    from service import rule_engine
    # First rule passes threshold, second fails
    rules = [
        {"id": "r_pass", "label": "car", "camera": "garage", "action": "summarize", "count": 2},
        {"id": "r_fail", "label": "car", "camera": "garage", "action": "summarize", "count": 5},
    ]
    event = {"label": "car", "camera": "garage", "num_vehicles": 3}
    with patch("service.rule_engine.get_rules", AsyncMock(return_value=rules)), \
         patch("service.rule_engine.dispatch_action", AsyncMock(return_value={"done": True})) as dispatch, \
         patch("service.rule_engine.store_response", AsyncMock()) as store:
        await rule_engine.process_event(event)
        # Only first matching rule should dispatch
        assert dispatch.await_count == 1
        assert store.await_count == 1
        assert event.get("rule_id") == "r_pass"

@pytest.mark.asyncio
async def test_process_event_threshold_pedestrian_missing_and_present():
    from service import rule_engine
    # One rule with threshold but event missing count -> skip
    # Another rule with count and event count present -> dispatch
    # First rule has higher threshold than provided count -> skipped.
    # Second rule passes -> dispatched.
    rules = [
        {"id": "r_skip", "label": "pedestrian", "camera": "front", "action": "search", "count": 5},
        {"id": "r_ok", "label": "pedestrian", "camera": "front", "action": "search", "count": 2},
    ]
    # Provide count only second time by running two events
    event_missing = {"label": "pedestrian", "camera": "front"}
    event_present = {"label": "pedestrian", "camera": "front", "num_pedestrians": 3}
    with patch("service.rule_engine.get_rules", AsyncMock(return_value=rules)), \
         patch("service.rule_engine.dispatch_action", AsyncMock(return_value={"result": "ok"})) as dispatch, \
         patch("service.rule_engine.store_response", AsyncMock()) as store:
        await rule_engine.process_event(event_missing)
        await rule_engine.process_event(event_present)
        # Only second event triggers dispatch
        assert dispatch.await_count == 1
        assert event_present.get("rule_id") == "r_ok"


@pytest.mark.asyncio
async def test_process_event_region_metadata_match_and_mismatch():
    from service import rule_engine

    rules = [
        {
            "id": "r_region",
            "label": "region_event",
            "action": "add to search",
            "source": "scenescape",
            "region_id": "region-001",
            "scene_id": "scene-001",
        }
    ]
    good_event = {
        "label": "region_event",
        "region_id": "region-001",
        "scene_id": "scene-001",
        "camera": "si1-camera1",
        "start_time": 1.0,
        "end_time": 2.0,
    }
    bad_event = {
        "label": "region_event",
        "region_id": "region-XYZ",
        "scene_id": "scene-001",
        "camera": "si1-camera1",
        "start_time": 1.0,
        "end_time": 2.0,
    }

    with patch("service.rule_engine.get_rules", AsyncMock(return_value=rules)), \
         patch("service.rule_engine.dispatch_action", AsyncMock(return_value={"ok": True})) as dispatch, \
         patch("service.rule_engine.store_response", AsyncMock()) as store:
        await rule_engine.process_event(good_event, context={"source": "scenescape"})
        await rule_engine.process_event(bad_event, context={"source": "scenescape"})
        assert dispatch.await_count == 1
        assert store.await_count == 1


@pytest.mark.asyncio
async def test_process_event_region_rule_skips_camera_topic():
    from service import rule_engine

    rules = [
        {
            "id": "r_zone",
            "label": "region_event",
            "action": "add to search",
            "source": "scenescape",
            "region_id": "region-001",
            "scene_id": "scene-001",
        }
    ]
    camera_event = {
        "label": "vehicle",
        "camera": "si1-camera1",
        "num_vehicles": 3,
        "num_pedestrians": 0,
    }

    with patch("service.rule_engine.get_rules", AsyncMock(return_value=rules)), \
         patch("service.rule_engine.dispatch_action", AsyncMock(return_value={"ok": True})) as dispatch, \
         patch("service.rule_engine.store_response", AsyncMock()) as store:
        await rule_engine.process_event(
            camera_event,
            context={"source": "scenescape", "topic": "scenescape/data/camera/camera1"},
        )
        dispatch.assert_not_called()
        store.assert_not_called()


@pytest.mark.asyncio
async def test_process_event_camera_rule_skips_region_topic():
    from service import rule_engine

    rules = [
        {
            "id": "r_cam",
            "label": "vehicle",
            "camera": "si1-camera1",
            "action": "add to search",
            "source": "scenescape",
        }
    ]
    region_event = {
        "label": "vehicle",
        "camera": "si1-camera1",
        "scene_id": "scene-001",
        "region_id": "region-001",
    }

    with patch("service.rule_engine.get_rules", AsyncMock(return_value=rules)), \
         patch("service.rule_engine.dispatch_action", AsyncMock(return_value={"ok": True})) as dispatch, \
         patch("service.rule_engine.store_response", AsyncMock()) as store:
        await rule_engine.process_event(
            region_event,
            context={"source": "scenescape", "topic": "scenescape/event/region/scene-001/region-001/count"},
        )
        dispatch.assert_not_called()
        store.assert_not_called()


@pytest.mark.asyncio
async def test_process_event_region_thresholds_multi_label_single_rule():
    from service import rule_engine

    rules = [
        {
            "id": "r_region_multi",
            "label": "region_event",
            "action": "add to search",
            "source": "scenescape",
            "region_id": "region-001",
            "scene_id": "scene-001",
            "region_thresholds": {"vehicle": 2, "pedestrian": 1},
        }
    ]

    matching_event = {
        "label": "region_event",
        "region_id": "region-001",
        "scene_id": "scene-001",
        "num_vehicles": 3,
        "num_pedestrians": 2,
    }
    non_matching_event = {
        "label": "region_event",
        "region_id": "region-001",
        "scene_id": "scene-001",
        "num_vehicles": 1,
        "num_pedestrians": 2,
    }

    with patch("service.rule_engine.get_rules", AsyncMock(return_value=rules)), \
         patch("service.rule_engine.dispatch_action", AsyncMock(return_value={"ok": True})) as dispatch, \
         patch("service.rule_engine.store_response", AsyncMock()) as store:
        await rule_engine.process_event(
            matching_event,
            context={"source": "scenescape", "topic": "scenescape/event/region/scene-001/region-001/count"},
        )
        await rule_engine.process_event(
            non_matching_event,
            context={"source": "scenescape", "topic": "scenescape/event/region/scene-001/region-001/count"},
        )

        assert dispatch.await_count == 1
        assert store.await_count == 1
