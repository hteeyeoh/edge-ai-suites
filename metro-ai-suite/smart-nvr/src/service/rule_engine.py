# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
from service.redis_store import get_rules, store_response
from service.dispatcher import dispatch_action
import logging
from fastapi import Request

logger = logging.getLogger(__name__)


async def process_event(event: dict, context: dict = None):
    """
    Process incoming events against configured rules and dispatch actions for matches.

    Args:
        event: Event data containing label, camera, timestamps, and optional count data
        context: Optional context with source information and topic details

    Returns:
        None: Actions are dispatched asynchronously for matching rules
    """
    logger.info("Processing Event.")
    if context:
        logger.info(f"Event context: {context}")

    logger.info(f"Detected label: {event.get('label')}")
    rules = await get_rules()
    logger.info(f"Loaded {len(rules)} rules")

    for rule in rules:
        logger.info(f"Evaluating rule: {rule}")
        event_topic = ((context or {}).get("topic") or "") if context else ""
        is_camera_topic = event_topic.startswith("scenescape/data/camera/")
        is_region_topic = event_topic.startswith("scenescape/event/region/")
        is_region_rule = bool(
            rule.get("region_id") or rule.get("scene_id") or rule.get("event_type")
        )
        if is_region_rule and event_topic and not event_topic.startswith("scenescape/event/region/"):
            logger.debug(
                f"Rule did not match: region topic mismatch (topic={event_topic})."
            )
            continue

        is_camera_rule = bool(rule.get("camera")) and not is_region_rule
        if is_camera_rule and event_topic and not is_camera_topic:
            logger.debug(
                f"Rule did not match: camera topic mismatch (topic={event_topic})."
            )
            continue

        if rule.get("label") != event.get("label"):
            logger.debug("Rule did not match: label mismatch.")
            continue

        if rule.get("camera") and rule["camera"] != event.get("camera"):
            logger.debug("Rule did not match: camera mismatch.")
            continue

        rule_source = rule.get("source")
        event_source = (context or {}).get("source") if context else None
        if rule_source and rule_source != event_source:
            logger.debug(
                f"Rule did not match: source mismatch (rule={rule_source}, event={event_source})."
            )
            continue

        if rule.get("region_id") and rule.get("region_id") != event.get("region_id"):
            logger.debug("Rule did not match: region_id mismatch.")
            continue

        if rule.get("scene_id") and rule.get("scene_id") != event.get("scene_id"):
            logger.debug("Rule did not match: scene_id mismatch.")
            continue

        if rule.get("event_type") and rule.get("event_type") != event.get("event_type"):
            logger.debug("Rule did not match: event_type mismatch.")
            continue

        threshold = rule.get("count")
        if threshold is not None:
            # count based on the event_label
            event_label = event.get("label", "").lower()
            if event_label == "pedestrian":
                event_count = event.get("num_pedestrians")
                count_type = "pedestrian"
            else:
                event_count = event.get("num_vehicles")
                count_type = "vehicle"

            if event_count is None:
                logger.debug(
                    f"Rule did not match: {count_type} count missing on event when rule requires it."
                )
                continue
            if event_count < threshold:
                logger.debug(
                    f"Rule did not match: {count_type} count {event_count} below threshold {threshold}."
                )
                continue

        logger.info("Match found.")
        event["rule_id"] = rule["id"]
        response = await dispatch_action(rule["action"], event)
        await store_response(rule["id"], response)
