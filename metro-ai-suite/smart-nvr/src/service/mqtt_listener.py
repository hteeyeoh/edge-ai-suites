# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional
import aiomqtt
from service.rule_engine import process_event
from datetime import datetime
from config import (
    MQTT_BROKER, MQTT_PORT, MQTT_TOPIC, MQTT_USER, MQTT_PASSWORD,
    SCENESCAPE_THROTTLE_INTERVAL, BROKER_RECONNECT_DELAY,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mqtt-listener")


async def process_scenescape_objects(objects, scenescape_camera, start_time, end_time, num_vehicles, num_pedestrians, msg_topic):
    """Process scenescape objects and trigger events for each object type."""
    for obj_type, obj_list in objects.items():
        if isinstance(obj_list, list) and obj_list:
            event_data = {
                "label": obj_type,
                "camera": scenescape_camera,
                "start_time": start_time,
                "end_time": end_time,
                "num_vehicles": num_vehicles,
                "num_pedestrians": num_pedestrians,
            }
            logger.info(f" Scenescape generated event: {event_data}")
            try:
                result = await process_event(event_data, context={"source": "scenescape", "topic": msg_topic})
                logger.info(f" process_event completed for {obj_type}: {result}")
            except Exception as e:
                logger.error(f" process_event failed for {obj_type}: {e}", exc_info=True)


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _to_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except Exception:
        return 0


def _first_non_empty(payload: Dict[str, Any], keys: List[str]) -> Any:
    for key in keys:
        if key in payload and payload.get(key) not in (None, ""):
            return payload.get(key)
    return None


def _extract_counts(payload: Dict[str, Any]) -> (int, int):
    objects = payload.get("objects", {}) if isinstance(payload, dict) else {}
    if isinstance(objects, dict):
        vehicles = objects.get("vehicle", [])
        pedestrians = objects.get("pedestrian", [])
        vehicle_count = len(vehicles) if isinstance(vehicles, list) else _to_int(vehicles)
        pedestrian_count = len(pedestrians) if isinstance(pedestrians, list) else _to_int(pedestrians)
    elif isinstance(objects, list):
        vehicle_count = 0
        pedestrian_count = 0
        for obj in objects:
            if not isinstance(obj, dict):
                continue
            obj_type = str(obj.get("type") or obj.get("category") or "").lower()
            if obj_type == "vehicle":
                vehicle_count += 1
            elif obj_type == "pedestrian":
                pedestrian_count += 1
    else:
        vehicle_count = 0
        pedestrian_count = 0

    counts = payload.get("counts", {})
    if isinstance(counts, dict):
        counted_vehicles = _to_int(counts.get("vehicle") or counts.get("vehicles"))
        counted_pedestrians = _to_int(counts.get("pedestrian") or counts.get("pedestrians"))
        vehicle_count = max(vehicle_count, counted_vehicles)
        pedestrian_count = max(pedestrian_count, counted_pedestrians)

    occupancy = payload.get("occupancy", {})
    if isinstance(occupancy, dict):
        occupancy_vehicles = _to_int(occupancy.get("vehicle") or occupancy.get("vehicles"))
        occupancy_pedestrians = _to_int(occupancy.get("pedestrian") or occupancy.get("pedestrians"))
        vehicle_count = max(vehicle_count, occupancy_vehicles)
        pedestrian_count = max(pedestrian_count, occupancy_pedestrians)

    return vehicle_count, pedestrian_count


def _extract_region_ids(payload: Dict[str, Any], topic: str) -> (str, str):
    scene_id = _first_non_empty(payload, ["scene_id", "sceneId", "scene_uid", "scene"])
    region_id = _first_non_empty(payload, ["region_id", "regionId", "region_uid", "region"])

    # Topic format in scenescape region stream includes .../region/<scene>/<region>/<event>
    parts = topic.split("/")
    if (not scene_id or not region_id) and len(parts) >= 6 and parts[0] == "scenescape" and parts[1] == "event" and parts[2] == "region":
        scene_id = scene_id or parts[3]
        region_id = region_id or parts[4]

    return str(scene_id) if scene_id else "", str(region_id) if region_id else ""


def _extract_region_cameras_from_visibility(payload: Dict[str, Any]) -> List[str]:
    cameras: List[str] = []

    def _add_visibility(value: Any) -> None:
        if isinstance(value, str) and value and value not in cameras:
            cameras.append(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item and item not in cameras:
                    cameras.append(item)

    _add_visibility(payload.get("visibility"))

    for key in ["objects", "entered", "exited", "participants"]:
        container = payload.get(key)
        if isinstance(container, list):
            entries = container
        elif isinstance(container, dict):
            entries = []
            for value in container.values():
                if isinstance(value, list):
                    entries.extend(value)
                elif isinstance(value, dict):
                    entries.append(value)
        else:
            continue

        for item in entries:
            if not isinstance(item, dict):
                continue
            _add_visibility(item.get("visibility"))

            nested_object = item.get("object")
            if isinstance(nested_object, dict):
                _add_visibility(nested_object.get("visibility"))

    return cameras


def _to_epoch_seconds(value: Any) -> Optional[float]:
    if value is None:
        return None

    numeric_value = _to_float(value)
    if numeric_value is not None:
        return numeric_value

    if isinstance(value, str):
        converted = iso_to_frigate_timestamp(value)
        return _to_float(converted)

    return None


def _extract_latest_region_entered_time(payload: Dict[str, Any], region_id: str) -> Optional[float]:
    objects = payload.get("objects") if isinstance(payload, dict) else None
    if not isinstance(objects, list):
        return None

    latest_entered: Optional[float] = None
    for obj in objects:
        if not isinstance(obj, dict):
            continue

        regions = obj.get("regions")
        if not isinstance(regions, dict) or not regions:
            continue

        region_entries: List[Dict[str, Any]] = []
        if region_id and isinstance(regions.get(region_id), dict):
            region_entries.append(regions.get(region_id))
        else:
            for region_info in regions.values():
                if isinstance(region_info, dict):
                    region_entries.append(region_info)

        for region_info in region_entries:
            entered_raw = _first_non_empty(region_info, ["entered", "entered_time", "entry_time"])
            entered_ts = _to_epoch_seconds(entered_raw)
            if entered_ts is None:
                continue
            if latest_entered is None or entered_ts > latest_entered:
                latest_entered = entered_ts

    return latest_entered


# Entered-time window is biased toward the past since Frigate needs time to finalize recordings.
REGION_CLIP_PRE_ROLL_SECONDS = 8
REGION_CLIP_POST_ROLL_SECONDS = 2


def _extract_clip_window(payload: Dict[str, Any], timestamp: float, region_id: str = "") -> (float, float):
    latest_entered = _extract_latest_region_entered_time(payload, region_id)
    if latest_entered is not None:
        return (
            latest_entered - REGION_CLIP_PRE_ROLL_SECONDS,
            latest_entered + REGION_CLIP_POST_ROLL_SECONDS,
        )

    explicit_start = _to_float(_first_non_empty(payload, ["start_time", "startTime"]))
    explicit_end = _to_float(_first_non_empty(payload, ["end_time", "endTime"]))
    if explicit_start is not None and explicit_end is not None and explicit_end > explicit_start:
        return explicit_start, explicit_end

    # Keep clip extraction behavior consistent with existing scenescape handling.
    return timestamp - 15, timestamp - 5


def _derive_region_labels(
    payload: Dict[str, Any],
    num_vehicles: int,
    num_pedestrians: int,
) -> List[str]:
    derived_labels: List[str] = []

    def _add_label(label_name: str) -> None:
        if label_name and label_name not in derived_labels:
            derived_labels.append(label_name)

    entered_raw = _first_non_empty(payload, ["entered", "entered_count", "entry_count", "entries"])
    exited_raw = _first_non_empty(payload, ["exited", "exited_count", "exit_count", "exits"])
    entered = len(entered_raw) if isinstance(entered_raw, list) else _to_int(entered_raw)
    exited = len(exited_raw) if isinstance(exited_raw, list) else _to_int(exited_raw)
    has_activity = (
        num_vehicles > 0
        or num_pedestrians > 0
        or entered > 0
        or exited > 0
        or bool(payload.get("participants"))
    )
    if has_activity:
        _add_label("region_event")

    return derived_labels


async def process_scenescape_region_payload(payload, topic, broker_id=None):
    num_vehicles, num_pedestrians = _extract_counts(payload)

    iso_timestamp = str(_first_non_empty(payload, ["timestamp", "event_time", "time"]) or "")
    formatted_timestamp = iso_to_frigate_timestamp(iso_timestamp)
    timestamp_value = _to_float(formatted_timestamp)
    if timestamp_value is None:
        logger.warning(f"Could not parse region-event timestamp: {iso_timestamp}")
        return

    scene_id, region_id = _extract_region_ids(payload, topic)
    region_name = str(_first_non_empty(payload, ["region_name", "regionName", "zone_name", "name"]) or region_id)
    scene_name = str(_first_non_empty(payload, ["scene_name", "sceneName"]) or scene_id)

    raw_cameras = _extract_region_cameras_from_visibility(payload)
    if not raw_cameras:
        logger.info(
            " Scenescape region payload ignored: missing visibility cameras "
            f"(topic={topic}, region_id={region_id}, region_name={region_name})"
        )
        return

    scenescape_cameras = [f"{broker_id}-{raw_camera}" if broker_id else str(raw_camera) for raw_camera in raw_cameras]

    start_time, end_time = _extract_clip_window(payload, timestamp_value, region_id)
    region_labels = _derive_region_labels(
        payload,
        num_vehicles,
        num_pedestrians,
    )

    if not region_labels:
        logger.info(
            " Scenescape region payload ignored: no derived labels "
            f"(topic={topic}, vehicles={num_vehicles}, pedestrians={num_pedestrians})"
        )
        return

    for scenescape_camera in scenescape_cameras:
        for derived_label in region_labels:
            event_data = {
                "label": derived_label,
                "camera": scenescape_camera,
                "start_time": start_time,
                "end_time": end_time,
                "num_vehicles": num_vehicles,
                "num_pedestrians": num_pedestrians,
                "scene_id": scene_id,
                "scene_name": scene_name,
                "region_id": region_id,
                "region_name": region_name,
            }
            logger.info(f" Scenescape region event: {topic} | Derived event: {event_data}")
            try:
                result = await process_event(event_data, context={"source": "scenescape", "topic": topic})
                logger.info(
                    f" process_event completed for region event {derived_label} "
                    f"on camera {scenescape_camera}: {result}"
                )
            except Exception as e:
                logger.error(
                    f" process_event failed for region event {derived_label} "
                    f"on camera {scenescape_camera}: {e}",
                    exc_info=True,
                )

# Convert ISO 8601 timestamp to float seconds since epoch
def iso_to_frigate_timestamp(iso_timestamp: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        return f"{dt.timestamp():.6f}"
    except Exception as e:
        logger.warning(f"Failed to parse timestamp {iso_timestamp}: {e}")
        return iso_timestamp

throttle_state = {"last_processed": 0}


async def handle_frigate_message(payload, topic):
    event_data = payload.get("after") or payload.get("before") or {}
    logger.info(f" Message received on topic: {topic} at {event_data.get('frame_time')}")
    label = event_data.get("label")
    camera_name = event_data.get("camera")
    start_time = event_data.get("start_time")
    end_time = event_data.get("end_time")

    if label and camera_name and start_time and end_time and (end_time - start_time) >= 10:
        logger.info(
            f" Event label: {label} |  Camera: {camera_name} |  Start: {start_time} |  End: {end_time}"
        )
        try:
            result = await process_event(event_data, context={"source": "frigate", "topic": topic})
            logger.info(f" process_event completed: {result}")
        except Exception as e:
            logger.error(f" process_event failed: {e}", exc_info=True)


async def handle_scenescape_message(payload, topic, state=None, broker_id=None):
    if state is None:
        state = throttle_state
    interval = state.get("interval", SCENESCAPE_THROTTLE_INTERVAL)
    now = time.time()
    if now - state["last_processed"] < interval:
        return
    state["last_processed"] = now

    if topic.startswith("scenescape/event/region/"):
        await process_scenescape_region_payload(payload, topic, broker_id=broker_id)
        return

    objects = payload.get("objects", {})
    vehicle_list = []
    pedestrian_list = []
    if isinstance(objects, dict):
        vehicle_list = objects.get("vehicle", [])
        pedestrian_list = objects.get("pedestrian", [])
    num_vehicles = len(vehicle_list)
    num_pedestrians = len(pedestrian_list)
    if num_vehicles <= 0 and num_pedestrians <= 0:
        return

    iso_timestamp = payload.get("timestamp", "")
    logger.info(f" Scenescape raw timestamp: {iso_timestamp}")
    formatted_timestamp = iso_to_frigate_timestamp(iso_timestamp)
    raw_camera = payload.get("id")
    scenescape_camera = f"{broker_id}-{raw_camera}" if broker_id else raw_camera

    start_time = float(formatted_timestamp) - 15
    end_time = float(formatted_timestamp) - 5

    logger.info(f" Scenescape event: {topic} | Camera: {scenescape_camera} | Vehicles: {num_vehicles} | Pedestrians: {num_pedestrians} | Timestamp: {formatted_timestamp} | Clip: {start_time}-{end_time} | Throttle: {SCENESCAPE_THROTTLE_INTERVAL}s")
    await process_scenescape_objects(objects, scenescape_camera, start_time, end_time, num_vehicles, num_pedestrians, topic)


async def start_frigate_client():
    while True:
        try:
            async with aiomqtt.Client(
                hostname=MQTT_BROKER,
                port=MQTT_PORT,
                username=MQTT_USER or None,
                password=MQTT_PASSWORD or None,
            ) as client:
                await client.subscribe(MQTT_TOPIC)
                logger.info(f" Subscribed to Frigate topic: {MQTT_TOPIC} at {MQTT_BROKER}:{MQTT_PORT}")
                async for message in client.messages:
                    topic = str(message.topic)
                    try:
                        payload = json.loads(message.payload.decode("utf-8", errors="ignore"))
                        if topic.startswith("frigate/"):
                            await handle_frigate_message(payload, topic)
                        else:
                            logger.warning(f" Unknown topic: {topic}")
                    except json.JSONDecodeError as e:
                        logger.error(f" Failed to decode MQTT message: {e}")
                    except Exception as e:
                        logger.error(f" Exception processing MQTT message: {e}", exc_info=True)
        except aiomqtt.MqttError as e:
            logger.error(f" Frigate MQTT connection error: {e}; reconnecting in {BROKER_RECONNECT_DELAY}s")
            await asyncio.sleep(BROKER_RECONNECT_DELAY)

