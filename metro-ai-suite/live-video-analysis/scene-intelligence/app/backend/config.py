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


def _frame_size_default(key: str, default: tuple[int, int]) -> tuple[int, int]:
    parsed = _frame_size(key)
    return parsed if parsed is not None else default


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
    VLM_MAX_TOKENS: int = _int("VLM_MAX_TOKENS", 128)

    # VLM NPU-specific configuration. Only used when VLM_DEVICE=NPU.
    NPU_MAX_PROMPT_LEN = _int("NPU_MAX_PROMPT_LEN", 1024)
    NPU_MIN_RESPONSE_LEN = _int("NPU_MIN_RESPONSE_LEN", 512)

    # Optional pre-inference frame resize for VLM input. Independent of
    # SEGMENT_DIM_* below — applied on top of whatever the segment encode
    # resolution is. Leave empty to caption at the segment's encode size.
    # Format: WIDTHxHEIGHT (for example 640x360).

    # Optional pre-inference frame resize for VLM input. Leave empty to keep
    # original sampled frame size. Format: WIDTHxHEIGHT (for example 640x360).
    VLM_FRAME_RESIZE: tuple[int, int] | None = _frame_size("VLM_FRAME_RESIZE")

    # Benchmarked segment recording (encode) dimensions per source aspect
    # ratio bucket; the closest bucket to the source's aspect ratio is used
    # (see _calculate_scaled_dimensions in stream_manager.py). This only sets
    # the .mp4 encode resolution — it does not affect VLM input size, which is
    # controlled independently by VLM_FRAME_RESIZE above. Format: WIDTHxHEIGHT.
    SEGMENT_DIM_1_1: tuple[int, int] = _frame_size_default("SEGMENT_DIM_1_1", (448, 448))
    SEGMENT_DIM_4_3: tuple[int, int] = _frame_size_default("SEGMENT_DIM_4_3", (512, 384))
    SEGMENT_DIM_16_9: tuple[int, int] = _frame_size_default("SEGMENT_DIM_16_9", (576, 320))

    # Max number of concurrent streams the registry will accept.
    MAX_STREAMS: int = _int("MAX_STREAMS", 8)

    # Rolling buffer cap: max finalized .mp4 segments retained on disk per
    # stream. Once a new segment finalizes and pushes the count over this,
    # the oldest finalized segment (and its frame-registry entries) is
    # deleted — segments roll forever, the writer never stops on its own.
    # 0 disables the cap (unbounded, segments accumulate indefinitely).
    MAX_SEGMENTS: int = _int("MAX_SEGMENTS", 20)

    # Hard cap on finalized segments retained on disk per stream; oldest is
    # deleted once a new segment finalizes and pushes the count over this.
    # 0 disables the cap (unbounded). Retained video per stream is roughly
    # SEGMENT_MAX_ON_DISK * SEGMENT_TIME_SECONDS seconds (default: 50 * 15s = 750s / ~12.5 min).
    SEGMENT_MAX_ON_DISK: int = _int("SEGMENT_MAX_ON_DISK", 5)
    VLM_MAX_INFERENCES: int = _int("VLM_MAX_INFERENCES", 20)

    # ---- Segment writer + frame metadata registry (for deep-analysis handoff) ----
    # Directory where rolling .mp4 segments are written, per stream.
    SEGMENT_OUTPUT_DIR: str = os.getenv("SEGMENT_OUTPUT_DIR", "segments")

    # Length of each rolling segment file, in seconds.
    SEGMENT_TIME_SECONDS: int = _int("SEGMENT_TIME_SECONDS", 15)

    # Frames per second registered into the metadata registry, per stream.
    FRAME_SAMPLE_FPS: int = _int("FRAME_SAMPLE_FPS", 1)

    # Fixed per-stream cap on frame metadata records kept in memory; a stream's
    # own oldest record is evicted once it exceeds this, independent of other streams.
    # One record per sampled frame, so retained history is roughly
    # FRAME_REGISTRY_MAX_RECORDS_PER_STREAM / FRAME_SAMPLE_FPS seconds
    # (default: 500 / 1fps = 500s / ~8.3 min).
    FRAME_REGISTRY_MAX_RECORDS_PER_STREAM: int = _int("FRAME_REGISTRY_MAX_RECORDS_PER_STREAM", 500)

    # ---- Deep analyzer (multi-frame follow-up on a "Yes" alert verdict) ----
    # When true, a segment whose sampled frame gets a "Yes" verdict from the
    # fast VLM is handed to a second, video-capable model for a richer,
    # multi-frame confirmation. Independent pipeline/device from VLM_*.
    DEEP_ANALYZER_ENABLED: bool = _bool("DEEP_ANALYZER_ENABLED", True)

    # Model tree layout mirrors VLM_MODELS_DIR/VLM_MODEL:
    # <DEEP_ANALYZER_MODELS_DIR>/<device>/<DEEP_ANALYZER_MODEL>.
    DEEP_ANALYZER_MODELS_DIR: str = os.getenv("DEEP_ANALYZER_MODELS_DIR", "/models")
    DEEP_ANALYZER_MODEL: str = os.getenv("DEEP_ANALYZER_MODEL", "Qwen3.5-2B-int4-ov")
    DEEP_ANALYZER_DEVICE: str = os.getenv("DEEP_ANALYZER_DEVICE", "GPU")

    # Frames uniformly sampled from a finalized segment per deep-analysis run.
    DEEP_ANALYZER_MAX_FRAMES: int = _int("DEEP_ANALYZER_MAX_FRAMES", 8)
    DEEP_ANALYZER_MAX_TOKENS: int = _int("DEEP_ANALYZER_MAX_TOKENS", 128)

    # Deep-analyzer NPU-specific configuration. Only used when
    # DEEP_ANALYZER_DEVICE=NPU (mirrors NPU_MAX_PROMPT_LEN/NPU_MIN_RESPONSE_LEN above).
    DEEP_ANALYZER_NPU_MAX_PROMPT_LEN = _int("DEEP_ANALYZER_NPU_MAX_PROMPT_LEN", 4096)
    DEEP_ANALYZER_NPU_MIN_RESPONSE_LEN = _int("DEEP_ANALYZER_NPU_MIN_RESPONSE_LEN", 512)

    # Bounded cache size for the dedup/finalized-segment tracking sets in
    # backend.deep_analyzer (per-process, not per-stream).
    DEEP_ANALYZER_DEDUP_CACHE_SIZE: int = _int("DEEP_ANALYZER_DEDUP_CACHE_SIZE", 500)

    # The "finalized" signal is a best-effort prediction (see
    # StreamManager._notify_segment_finalized) — the muxer may not have
    # flushed the segment's trailer to disk yet when it fires. Retries give
    # that a moment to catch up before giving up on the segment.
    DEEP_ANALYZER_SEGMENT_READ_MAX_RETRIES: int = _int("DEEP_ANALYZER_SEGMENT_READ_MAX_RETRIES", 5)
    DEEP_ANALYZER_SEGMENT_READ_RETRY_DELAY: float = _float("DEEP_ANALYZER_SEGMENT_READ_RETRY_DELAY", 1.0)

    # Multi-frame confirmation prompt; {event} is substituted with the
    # stream's alert_event, same convention as ALERT_PROMPT_TEMPLATE.
    DEEP_ANALYZER_PROMPT_TEMPLATE: str = ("Explain the video in detail.")


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
