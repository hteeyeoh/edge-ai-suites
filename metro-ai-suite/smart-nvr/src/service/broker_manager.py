# SPDX-FileCopyrightText: (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
import asyncio
import json
import logging
import os
import ssl

import aiomqtt
import yaml

from config import (
    BROKERS_CONFIG_PATH,
    MAX_CONCURRENT_EVENTS,
    BROKER_RECONNECT_DELAY,
    NVR_SCENESCAPE_ENABLED,
    SCENESCAPE_MQTT_BROKER,
    SCENESCAPE_MQTT_PORT,
    SCENESCAPE_MQTT_TOPIC,
    SCENESCAPE_REGION_MQTT_TOPIC,
)
from model.broker import Broker
from service import redis_store
from service.mqtt_listener import handle_frigate_message, handle_scenescape_message

logger = logging.getLogger("broker-manager")

_tasks: dict[str, list[asyncio.Task]] = {}
_semaphore = asyncio.Semaphore(MAX_CONCURRENT_EVENTS)


def _tls_context() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _reason_code_to_int(code) -> int:
    """Best-effort conversion for aiomqtt/paho reason-code values."""
    if isinstance(code, int):
        return code

    value = getattr(code, "value", None)
    if isinstance(value, int):
        return value

    try:
        return int(code)
    except (TypeError, ValueError):
        return -1


async def _dispatch(broker: Broker, payload, topic, state):
    async with _semaphore:
        if broker.type == "frigate":
            await handle_frigate_message(payload, topic)
        else:
            # Tag region-stream messages so downstream camera-name derivation
            # (see mqtt_listener/vms_service) can distinguish them from camera
            # events even when both topics share a single broker connection.
            is_region = topic.startswith("scenescape/event/region/")
            dispatch_id = f"{broker.id}-region" if is_region else broker.id
            await handle_scenescape_message(payload, topic, state, broker_id=dispatch_id)


async def _run_broker_topic(broker: Broker, topic: str):
    """One dedicated MQTT connection per topic filter.

    Each topic gets its own connection (and throttle state) rather than sharing
    one client across camera_topic/region_topic: a burst on one topic must not
    delay or starve delivery/throttling of the other.
    """
    state = {"last_processed": 0, "interval": broker.throttle_interval}
    while True:
        try:
            async with aiomqtt.Client(
                hostname=broker.host,
                port=broker.port,
                tls_context=_tls_context() if broker.use_tls else None,
            ) as client:
                suback = await client.subscribe(topic, qos=1)
                granted = [_reason_code_to_int(code) for code in suback]

                # MQTT SUBACK return code 128 indicates a rejected subscription.
                if any(code == 128 for code in granted):
                    logger.error(
                        f"[{broker.id}] subscription rejected for topic {topic} at {broker.host}:{broker.port} (SUBACK={granted})"
                    )
                else:
                    logger.info(
                        f"[{broker.id}] subscribed to {topic} at {broker.host}:{broker.port} (SUBACK={granted})"
                    )
                async for message in client.messages:
                    msg_topic = str(message.topic)
                    try:
                        payload = json.loads(message.payload.decode("utf-8", errors="ignore"))
                        asyncio.create_task(_dispatch(broker, payload, msg_topic, state))
                    except json.JSONDecodeError as e:
                        logger.error(f"[{broker.id}] failed to decode message: {e}")
                    except Exception as e:
                        logger.error(f"[{broker.id}] error processing message: {e}", exc_info=True)
        except aiomqtt.MqttError as e:
            logger.error(
                f"[{broker.id}] connection error on {topic}: {e}; reconnecting in {BROKER_RECONNECT_DELAY}s"
            )
            await asyncio.sleep(BROKER_RECONNECT_DELAY)


def _as_broker(broker) -> Broker:
    return broker if isinstance(broker, Broker) else Broker(**broker)


async def start_broker(broker):
    broker = _as_broker(broker)
    await stop_broker(broker.id)
    if not broker.enabled:
        logger.info(f"[{broker.id}] disabled, not starting")
        return
    topics = broker.topics
    if not topics:
        logger.warning(f"[{broker.id}] no topics configured, not starting")
        return
    _tasks[broker.id] = [
        asyncio.create_task(_run_broker_topic(broker, topic)) for topic in topics
    ]


async def stop_broker(broker_id: str):
    tasks = _tasks.pop(broker_id, None)
    if not tasks:
        return
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass


async def restart_broker(broker):
    broker = _as_broker(broker)
    await start_broker(broker)


async def start_all_from_redis(request=None):
    for data in await redis_store.get_brokers(request):
        await start_broker(data)


async def stop_all():
    for broker_id in list(_tasks.keys()):
        await stop_broker(broker_id)


async def _topic_covered(topic: str, request=None) -> bool:
    return any(
        topic in (b.get("camera_topic"), b.get("region_topic"))
        for b in await redis_store.get_brokers(request)
    )


async def load_yaml_brokers(path: str = BROKERS_CONFIG_PATH, request=None):
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            logger.error("Invalid YAML in %s, skipping broker sync: %s", path, e)
            data = {}
        yaml_entries = data.get("brokers") or []
        yaml_ids = {e["id"] for e in yaml_entries}
        if yaml_ids:
            for b in await redis_store.get_brokers(request):
                if b["id"] not in yaml_ids:
                    await stop_broker(b["id"])
                    await redis_store.delete_broker(b["id"], request)
        for entry in yaml_entries:
            broker = Broker(**entry)
            await redis_store.save_broker(broker.id, broker.model_dump(), request)

    # Check camera/region topic coverage independently: brokers.yaml (e.g. reset by
    # setup.sh start) commonly already has a camera-topic si1, which previously
    # caused the region-topic seed below to be skipped because "a scenescape
    # broker" existed.
    if NVR_SCENESCAPE_ENABLED:
        has_camera = await _topic_covered(SCENESCAPE_MQTT_TOPIC, request)
        has_region = await _topic_covered(SCENESCAPE_REGION_MQTT_TOPIC, request)

        if not has_camera or not has_region:
            brokers_by_id = {b["id"]: b for b in await redis_store.get_brokers(request)}
            existing = brokers_by_id.get("si1")
            if existing:
                if not has_camera and not existing.get("camera_topic"):
                    existing["camera_topic"] = SCENESCAPE_MQTT_TOPIC
                if not has_region and not existing.get("region_topic"):
                    existing["region_topic"] = SCENESCAPE_REGION_MQTT_TOPIC
                await redis_store.save_broker("si1", existing, request)
                logger.info("Updated default si1 broker with missing scenescape topic(s) from environment")
            else:
                default_broker = Broker(
                    id="si1",
                    name="Smart Intersection 1",
                    host=SCENESCAPE_MQTT_BROKER,
                    port=SCENESCAPE_MQTT_PORT,
                    camera_topic=SCENESCAPE_MQTT_TOPIC,
                    region_topic=SCENESCAPE_REGION_MQTT_TOPIC,
                    type="scenescape",
                    use_tls=True,
                    rtsp_host=SCENESCAPE_MQTT_BROKER,
                )
                await redis_store.save_broker(
                    default_broker.id, default_broker.model_dump(), request
                )
                logger.info("Seeded default si1 broker (camera + region topics) from environment")

    await sync_yaml_from_redis(request=request, path=path)


_YAML_HEADER = (
    "# Scenescape-mode MQTT brokers. Active when NVR_SCENESCAPE=true.\n"
    "# host = MQTT broker IP only.\n"
    "# rtsp_host = optional SI RTSP stream IP, read by setup.sh to build the Frigate camera list.\n"
    "# rtsp_port = SI RTSP stream port, read by setup.sh. Required for start-nvr (or set SI{N}_RTSP_PORT); ignored by start (uses RTSP_STREAM_PORT).\n"
    "# Broker id must match the SI node prefix in Frigate camera names (e.g. si1 -> si1-camera1).\n"
)


async def sync_yaml_from_redis(request=None, path: str = BROKERS_CONFIG_PATH):
    """Persist current Redis brokers to YAML. Best-effort — errors are logged, not raised."""
    try:
        brokers = await redis_store.get_brokers(request)
        with open(path, "w") as f:
            f.write(_YAML_HEADER)
            yaml.safe_dump({"brokers": brokers}, f, default_flow_style=False, sort_keys=False)
        logger.info(f"Synced {len(brokers)} broker(s) to {path}")
    except OSError as e:
        logger.warning(f"Could not write brokers YAML at {path}: {e}")

