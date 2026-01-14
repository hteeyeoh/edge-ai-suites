# API Reference

The backend is a FastAPI application that serves REST APIs, an SSE stream for captions/metadata, and WebSocket endpoints for metrics.

## Interactive API docs

When the stack is running, FastAPI provides OpenAPI/Swagger UI at:

- `http://localhost:4173/docs`

(If you run the backend on a different host/port, adjust accordingly.)

## REST Endpoints

- `GET /api/models` — List available VLM models discovered under `ov_models/`
- `GET /api//detection-models` - List available object detection models discovered under `ov_detection_models/`
- `GET /api/pipelines` — List available pipeline configurations
- `POST /api/runs` — Start a new captioning pipeline
- `GET /api/runs` — List active runs
- `GET /api/runs/{run_id}` — Get run details
- `DELETE /api/runs/{run_id}` — Stop a pipeline

## Streaming Endpoints

### Server-Sent Events (SSE)

- `GET /api/runs/metadata-stream` — SSE stream for captions and run/metrics metadata

### WebSockets

- `ws://localhost:4173/ws/collector` — Metrics collector connection (single connection)
- `ws://localhost:4173/ws/clients` — Metrics broadcast to dashboard clients (multiple connections)

## Related docs

- [Get Started](./get-started.md)
- [Known Issues](./known-issues.md)
