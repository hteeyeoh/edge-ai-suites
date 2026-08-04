# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Runtime configuration loaded from environment variables."""

import logging
import os


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


class Settings:
    # ---- server ----
    PORT: int = _int("PORT", 9100)
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # ---- stream source ----
    # Primary RTSP (or file/http) source rendered under stream id "default".
    RTSP_URL: str = os.getenv("RTSP_URL", "")

    # Transport for RTSP sources: "tcp" (robust) or "udp" (lower latency).
    RTSP_TRANSPORT: str = os.getenv("RTSP_TRANSPORT", "tcp")

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

    # Max number of concurrent streams the registry will accept.
    MAX_STREAMS: int = _int("MAX_STREAMS", 8)


settings = Settings()


def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("libav", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
