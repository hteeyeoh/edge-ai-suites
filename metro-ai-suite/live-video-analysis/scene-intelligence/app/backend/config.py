# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Runtime configuration loaded from environment variables."""

import logging
import os
import re


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default


def _bool(key: str, default: bool) -> bool:
    val = os.getenv(key, "")
    if not val:
        return default
    return val.strip().lower() in ("1", "true", "yes")


def _frame_size(key: str) -> tuple[int, int] | None:
    raw = os.getenv(key, "").strip()
    if not raw:
        return None

    match = re.fullmatch(r"(\d+)\s*[xX,]\s*(\d+)", raw)
    if not match:
        logging.getLogger(__name__).warning(
            "Invalid %s='%s'; expected WIDTHxHEIGHT (for example 640x360)",
            key,
            raw,
        )
        return None

    width = int(match.group(1))
    height = int(match.group(2))
    if width <= 0 or height <= 0:
        logging.getLogger(__name__).warning(
            "Invalid %s='%s'; width/height must be positive integers",
            key,
            raw,
        )
        return None
    return (width, height)


class Settings:
    # ---- server ----
    PORT: int = _int("PORT", 9100)
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # ---- stream source ----
    # Socket open/read timeout in seconds for PyAV.
    RTSP_TIMEOUT: float = _float("RTSP_TIMEOUT", 15.0)

    # ---- WebRTC rendering (via MediaMTX relay) ----
    # When true, each source is remuxed (stream-copy) into MediaMTX with PyAV
    # so the browser can subscribe over WebRTC (WHEP).
    WEBRTC_AUTO_PUBLISH: bool = _bool("WEBRTC_AUTO_PUBLISH", True)

    # RTSP base URL of the MediaMTX server the relay publishes to.
    # The stream id is appended as the path, e.g. rtsp://mediamtx:8554/default.
    WEBRTC_RELAY_URL: str = os.getenv("WEBRTC_RELAY_URL", "rtsp://mediamtx:8554")

    # Public WebRTC (WHEP) signaling base the browser connects to. When empty
    # the UI derives it from the page host and WEBRTC_SIGNALING_PORT.
    WEBRTC_SIGNALING_URL: str = os.getenv("WEBRTC_SIGNALING_URL", "")
    WEBRTC_SIGNALING_PORT: int = _int("WEBRTC_SIGNALING_PORT", 8889)

    # Metrics-manager SSE port used by the UI device-usage panel.
    METRICS_SERVICE_PORT: int = _int("METRICS_SERVICE_PORT", 9090)

    # ---- VLM inference (OpenVINO GenAI) ----
    # When true, each stream decodes sampled frames and captions them with the
    # OpenVINO GenAI VLM pipeline.
    VLM_ENABLED: bool = _bool("VLM_ENABLED", True)

    # Root of the mounted model tree. Models are organised per device as
    # <VLM_MODELS_DIR>/<device>/<VLM_MODEL>, e.g. /models/cpu/InternVL2-1B.
    VLM_MODELS_DIR: str = os.getenv("VLM_MODELS_DIR", "/models")
    VLM_MODEL: str = os.getenv("VLM_MODEL", "InternVL2-1B")

    # Inference device: CPU, GPU or NPU (selects the matching model subfolder).
    VLM_DEVICE: str = os.getenv("VLM_DEVICE", "CPU")

    # Alert prompt template used to construct the final VLM prompt from a
    # user-provided alert event (e.g. "fire", "accident").
    ALERT_PROMPT_TEMPLATE: str = (
        "Task: Determine whether the event {event} is present in this image. "
        'Use only visual evidence from this single frame. '
        'If the event is clearly present, reply "Yes". Otherwise, reply "No". '
        'Output exactly one word: "Yes" or "No".'
    )

    # Seconds between inferences per stream and the token budget per caption.
    VLM_INTERVAL: float = _float("VLM_INTERVAL", 5.0)
    VLM_MAX_TOKENS: int = _int("VLM_MAX_TOKENS", 100)

    # Optional pre-inference frame resize for VLM input. Leave empty to keep
    # original sampled frame size. Format: WIDTHxHEIGHT (for example 640x360).
    VLM_FRAME_RESIZE: tuple[int, int] | None = _frame_size("VLM_FRAME_RESIZE")

    # Max number of concurrent streams the registry will accept.
    MAX_STREAMS: int = _int("MAX_STREAMS", 8)

    # Testing limits: cap segments written / VLM inferences per stream so a
    # looping test RTSP source doesn't run (and write to disk) indefinitely.
    # 0 = unlimited.
    MAX_SEGMENTS: int = _int("MAX_SEGMENTS", 20)
    VLM_MAX_INFERENCES: int = _int("VLM_MAX_INFERENCES", 20)

    # ---- Segment cleanup (reclaim disk space once a segment is no longer needed) ----
    # Always retain this many of the most-recently finalized segments per
    # stream, regardless of VLM verdict/TTL, so a downstream deep analyzer
    # (M4) has time to pick them up before they're reclaimed.
    SEGMENT_RETENTION_GRACE: int = _int("SEGMENT_RETENTION_GRACE", 2)

    # Fallback for streams with no alert_event (no Yes/No verdict available):
    # reclaim a finalized segment once it's this many seconds old. 0 disables.
    SEGMENT_TTL_SECONDS: float = _float("SEGMENT_TTL_SECONDS", 300.0)

    # Hard backstop: max finalized segments retained on disk per stream,
    # regardless of verdict/TTL (evicts oldest first). 0 disables the cap.
    SEGMENT_MAX_ON_DISK: int = _int("SEGMENT_MAX_ON_DISK", 50)

    # ---- Segment writer + frame metadata registry (for deep-analysis handoff) ----
    # Directory where rolling .mp4 segments are written, per stream.
    SEGMENT_OUTPUT_DIR: str = os.getenv("SEGMENT_OUTPUT_DIR", "segments")

    # Length of each rolling segment file, in seconds.
    SEGMENT_TIME_SECONDS: int = _int("SEGMENT_TIME_SECONDS", 15)

    # Frames per second registered into the metadata registry, per stream.
    FRAME_SAMPLE_FPS: int = _int("FRAME_SAMPLE_FPS", 1)

    # Max frame metadata records kept in memory before oldest are evicted.
    FRAME_REGISTRY_MAX_RECORDS: int = _int("FRAME_REGISTRY_MAX_RECORDS", 3000)


settings = Settings()


def build_alert_prompt(alert_event: str) -> str:
    """Build a binary-response VLM prompt from a user-provided alert event."""
    event_text = " ".join(str(alert_event or "").strip().split())
    if not event_text:
        raise ValueError("'alert_event' must not be empty")
    if re.search(r"[,;|/]", event_text):
        raise ValueError("Only one alert event is supported per stream")

    return settings.ALERT_PROMPT_TEMPLATE.format(event=event_text)


def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("libav", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
