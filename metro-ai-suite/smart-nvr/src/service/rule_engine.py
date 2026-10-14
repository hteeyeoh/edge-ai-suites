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
            rule.get("region_id") or rule.get("scene_id")
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

        rule_event_type = str(rule.get("event_type") or "count").lower()

        if is_region_rule and rule_event_type == "dwell_time":
            rule_label = str(rule.get("label") or "").lower()
            if rule_label not in ("vehicle", "pedestrian"):
                logger.debug("Rule did not match: dwell_time requires label vehicle or pedestrian.")
                continue

            duration_threshold = rule.get("duration_seconds")
            if duration_threshold is None:
                logger.debug("Rule did not match: dwell_time missing duration_seconds.")
                continue

            try:
                duration_threshold_value = float(duration_threshold)
            except (TypeError, ValueError):
                logger.debug("Rule did not match: dwell_time threshold is invalid.")
                continue

            max_dwell_by_label = event.get("max_dwell_by_label")
            if not isinstance(max_dwell_by_label, dict):
                logger.debug("Rule did not match: dwell_time event missing max_dwell_by_label.")
                continue

            dwell_value = max_dwell_by_label.get(rule_label)
            if dwell_value is None:
                logger.debug("Rule did not match: dwell_time event missing dwell for label %s.", rule_label)
                continue

            try:
                dwell_value = float(dwell_value)
            except (TypeError, ValueError):
                logger.debug("Rule did not match: dwell_time dwell value is invalid.")
                continue

            if dwell_value < duration_threshold_value:
                logger.debug(
                    "Rule did not match: dwell %.3f below threshold %.3f.",
                    dwell_value,
                    duration_threshold_value,
                )
                continue

        if is_region_rule and rule_event_type == "near_miss":
            distance_threshold = rule.get("near_miss_distance_m")
            if distance_threshold is None:
                logger.debug("Rule did not match: near_miss missing near_miss_distance_m.")
                continue

            try:
                distance_threshold_value = float(distance_threshold)
            except (TypeError, ValueError):
                logger.debug("Rule did not match: near_miss threshold is invalid.")
                continue

            event_distance = event.get("near_miss_min_distance_m")
            if event_distance is None:
                logger.debug("Rule did not match: near_miss event missing near_miss_min_distance_m.")
                continue

            try:
                event_distance_value = float(event_distance)
            except (TypeError, ValueError):
                logger.debug("Rule did not match: near_miss event distance is invalid.")
                continue

            if event_distance_value >= distance_threshold_value:
                logger.debug(
                    "Rule did not match: near_miss distance %.3f above/equal threshold %.3f.",
                    event_distance_value,
                    distance_threshold_value,
                )
                continue

        region_thresholds = rule.get("region_thresholds") if is_region_rule else None
        if region_thresholds and not (is_region_rule and rule_event_type in {"dwell_time", "near_miss"}):
            matched_all_thresholds = True
            for label_name, threshold in region_thresholds.items():
                normalized_label = str(label_name).lower()
                event_count = (
                    event.get("num_pedestrians")
                    if normalized_label == "pedestrian"
                    else event.get("num_vehicles")
                )

                if event_count is None:
                    logger.debug(
                        "Rule did not match: %s count missing on event when rule requires it.",
                        normalized_label,
                    )
                    matched_all_thresholds = False
                    break

                if event_count < int(threshold):
                    logger.debug(
                        "Rule did not match: %s count %s below threshold %s.",
                        normalized_label,
                        event_count,
                        threshold,
                    )
                    matched_all_thresholds = False
                    break

            if not matched_all_thresholds:
                continue
        else:
            if not (is_region_rule and rule_event_type in {"dwell_time", "near_miss"}) and rule.get("label") != event.get("label"):
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

        threshold = rule.get("count")
        if threshold is not None and not region_thresholds:
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
