# Scene Intelligence

## Overview

Scene Intelligence is a real-time video monitoring application that ingests RTSP camera streams, renders low-latency browser playback over WebRTC, and runs AI-based alert detection with deep multi-frame analysis.

At runtime, each stream is managed by a PyAV worker that:

- decodes once per frame from the RTSP source,
- re-encodes a downscaled relay to MediaMTX for WebRTC playback,
- samples frames for single-frame VLM alert checks,
- writes rolling video segments for deep-analysis follow-up,
- uploads confirmed alert artifacts (video + metadata) to SeaweedFS (S3-compatible storage).

The application includes a FastAPI backend and a browser UI for stream control, live playback, system metrics, and alert history/details.

## Current Implementation

### Main components

- app/main.py: FastAPI app wiring stream, alert, health, registry, and runtime-config routes.
- app/backend/services/stream_manager.py: per-stream ingest loop, WebRTC relay output, frame sampling, segmentation, and alert trigger path.
- app/backend/services/vlm.py: single-frame VLM inference used as the fast alert gate.
- app/backend/services/deep_analyzer.py: multi-frame follow-up analysis for segments that triggered a positive alert.
- app/backend/services/object_storage.py: SeaweedFS/S3 upload of alert video and JSON sidecar.
- app/backend/services/alert_index.py: in-memory alert index with lazy hydration from stored sidecars.
- app/ui/: dashboard for stream management, playback, device telemetry, and alert investigation.

### Runtime services (Docker Compose)

- scene-intelligence: FastAPI app + UI.
- mediamtx: RTSP ingest target and WebRTC/WHEP playback service.
- coturn: TURN server used by WebRTC.
- metrics-manager: SSE metrics endpoint for CPU, RAM, GPU, and NPU visualization.
- seaweedfs: S3-compatible object storage for alert artifacts.

## Architecture

```text
RTSP Camera
   |
   v
StreamManager (PyAV)
   |-- downscaled H.264 relay -> MediaMTX -> WHEP/WebRTC -> Browser UI
   |-- frame sampling -> VLM (Yes/No alert gate)
   |-- rolling segments + frame registry
                          |
                          v
                Deep Analyzer (multi-frame)
                          |
                          v
        SeaweedFS (video + analysis sidecar JSON)
                          |
                          v
               Alert APIs + UI alert history/details
```

## API Endpoints

### Core app

| Method | Path | Description |
| --- | --- | --- |
| GET | / | UI dashboard |
| GET | /health | Liveness with uptime and active stream count |
| GET | /runtime-config.js | Runtime config consumed by the UI |

### Stream management

| Method | Path | Description |
| --- | --- | --- |
| GET | /streams | List active streams and runtime state |
| POST | /streams | Add stream and alert configuration |
| DELETE | /streams/{stream_id} | Stop and remove stream |

POST /streams payload:

```json
{
  "stream_id": "camera-lobby",
  "url": "rtsp://<camera-host>/<path>",
  "alert_event": "fire"
}
```

Notes:

- alert_event is required.
- only one alert event is supported per stream.

### Alert APIs

| Method | Path | Description |
| --- | --- | --- |
| GET | /streams/{stream_id}/alerts?limit=20&offset=0 | Paginated alert list |
| GET | /streams/{stream_id}/alerts/{frame_id} | Alert detail and metadata |
| GET | /streams/{stream_id}/alerts/{frame_id}/video | Alert video playback (mp4) |

### Frame registry APIs

| Method | Path | Description |
| --- | --- | --- |
| GET | /registry/stats | Registry summary |
| GET | /registry/stream/{stream_id}?limit=50 | Latest registry records for stream |
| GET | /registry/frame/{frame_id} | Lookup specific sampled frame record |

### WebRTC playback handshake

The browser UI opens WebRTC playback by posting SDP to MediaMTX WHEP:

- POST /{stream_id}/whep (served by MediaMTX, not by FastAPI)

## Configuration

All runtime settings are controlled by environment variables in app/backend/config.py. Commonly tuned variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| PORT | 9100 | FastAPI/UI port |
| RTSP_TIMEOUT | 15.0 | Input open/read timeout |
| WEBRTC_RELAY_URL | rtsp://mediamtx:8554 | Relay publish target |
| WEBRTC_SIGNALING_URL | (derived/optional) | Public WebRTC base URL |
| WEBRTC_SIGNALING_PORT | 8889 | MediaMTX WebRTC port |
| METRICS_SERVICE_PORT | 9090 | Metrics SSE service port |
| MAX_STREAMS | 8 | Max concurrent streams |
| ALERT_VLM_MODEL | InternVL2-1B | Single-frame VLM model name |
| ALERT_VLM_DEVICE | CPU | Device for VLM (CPU/GPU/NPU) |
| ALERT_VLM_INTERVAL | 5.0 | Seconds between inference attempts |
| SEGMENT_TIME_SECONDS | 15 | Segment duration |
| FRAME_SAMPLE_FPS | 1 | Frame sampling rate for registry |
| SEGMENT_MAX_ON_DISK | 50 | Per-stream segment retention cap |
| DEEP_ANALYZER_ENABLED | true | Enable multi-frame deep analysis |
| DEEP_ANALYZER_MODEL | Qwen3.5-2B-int4-ov | Deep analyzer model name |
| DEEP_ANALYZER_DEVICE | GPU | Device for deep analyzer |
| SEAWEEDFS_ENDPOINT_URL | http://seaweedfs:8333 | S3-compatible endpoint |
| SEAWEEDFS_BUCKET | scene-intelligence | Alert artifact bucket |
| S3_RETENTION_DAYS | 10 | Object retention/lifecycle window |

Model path note: both alert VLM and deep analyzer resolve models from the same fixed root (`/models`) via `VLM_MODELS_DIR`, with layout `<root>/<device>/<model>`.

## Model Download (One Time)

Download and convert the VLM model used for alert gating:

```bash
./model_download_scripts/download_models.sh \
  --model OpenGVLab/InternVL2-1B \
  --type vlm \
  --weight-format int8
```

Optional device-specific conversion:

```bash
./model_download_scripts/download_models.sh \
  --model OpenGVLab/InternVL2-1B \
  --type vlm \
  --weight-format int8 \
  --device <CPU|GPU|NPU>
```

Note: when device is NPU, conversion may require int4 quantization depending on model/tooling constraints.

## Run with Docker Compose

```bash
bash scripts/setup_env.sh
docker compose up -d --build
```

Then open:

- http://localhost:9100

Add a stream from the UI using:

- RTSP URL
- optional Stream ID
- required Alert Event

Equivalent API call:

```bash
curl -X POST http://localhost:9100/streams \
  -H 'Content-Type: application/json' \
  -d '{"stream_id":"camera-lobby","url":"rtsp://<camera-host>/<path>","alert_event":"fire"}'
```

## Operational Notes

- If no stream is configured, /health still reports healthy with streams_active=0.
- Alerts are indexed in memory and lazily rehydrated from SeaweedFS sidecar JSON records.
