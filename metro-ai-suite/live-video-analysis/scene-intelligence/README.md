# Scene Intelligence

RTSP ingestion and WebRTC rendering pipeline built with [PyAV](https://pyav.org).
This sample application consumes RTSP streams, remuxes them into
[MediaMTX](https://github.com/bluenviron/mediamtx) with PyAV (stream-copy, no
re-encode), and renders low-latency video in the browser over **WebRTC (WHEP)**.
It is the foundation for a later stage that adds a PyAV decode branch to forward
frames to a VLM model for inference.

## Architecture (Step 1)

```
RTSP source ──▶ PyAV remux (StreamManager) ──▶ MediaMTX ──WebRTC/WHEP──▶ Browser
                                                  ▲
                                             coturn (TURN)
```

- `app/backend/stream_manager.py` — PyAV relay that opens the source and
  remuxes packets into MediaMTX over RTSP, with auto-reconnect.
- `app/backend/registry.py` — thread-safe registry owning stream lifecycles.
- `app/main.py` — FastAPI app exposing `/streams`, `/health`, `/runtime-config.js`, and UI.
- `app/ui/` — dashboard that plays the stream via a WHEP WebRTC handshake.
- `mediamtx` — media server that serves the relayed stream over WebRTC.
- `coturn` — TURN server for WebRTC NAT traversal.

Video never leaves the compressed domain on the backend, so rendering stays
low-latency; PyAV remains the video engine end to end.

## Endpoints

| Service            | Method | Path                   | Description                          |
| ------------------ | ------ | ---------------------- | ------------------------------------ |
| scene-intelligence | GET    | `/`                    | Dashboard UI                         |
| scene-intelligence | GET    | `/streams`             | List active streams and health       |
| scene-intelligence | POST   | `/streams`             | Add a stream `{ "url", "stream_id" }`|
| scene-intelligence | DELETE | `/streams/{stream_id}` | Remove a stream                      |
| scene-intelligence | GET    | `/runtime-config.js`   | WebRTC signaling config for the UI   |
| scene-intelligence | GET    | `/health`              | Liveness probe                       |
| mediamtx           | POST   | `/{stream_id}/whep`    | WebRTC (WHEP) playback handshake     |

## Configuration

All settings are environment variables (see `app/backend/config.py`):

| Variable               | Default                 | Description                                        |
| ---------------------- | ----------------------- | -------------------------------------------------- |
| `PORT`                 | `9100`                  | App HTTP port                                      |
| `RTSP_URL`             | *(none)*                | Primary source registered as `default`             |
| `RTSP_TRANSPORT`       | `tcp`                   | RTSP transport (`tcp` or `udp`)                    |
| `RTSP_TIMEOUT`         | `15.0`                  | PyAV open/read timeout (seconds)                   |
| `WEBRTC_AUTO_PUBLISH`  | `true`                  | Relay each source into MediaMTX                    |
| `WEBRTC_RELAY_URL`     | `rtsp://mediamtx:8554`  | MediaMTX RTSP base the relay publishes to          |
| `WEBRTC_SIGNALING_URL` | *(derived)*             | Public WHEP base for the browser (host:port)       |
| `WEBRTC_SIGNALING_PORT`| `8889`                  | MediaMTX WebRTC port                               |
| `MAX_STREAMS`          | `8`                     | Maximum concurrent streams                         |

## Run with Docker Compose

```bash
export RTSP_URL="rtsp://user:pass@camera-host:554/stream"
source ./env.sh          # sets HOST_IP + WebRTC signaling URL
docker compose up -d --build
```

Open http://localhost:9100 to view the live WebRTC stream.

> `source ./env.sh` detects the host LAN IP so browsers on other machines can
> reach MediaMTX and coturn. Without it, playback works only from `localhost`.

## Run locally (backend only)

MediaMTX and coturn still run in containers; run the app against them:

```bash
cd app
uv pip install -r pyproject.toml
RTSP_URL="rtsp://..." WEBRTC_RELAY_URL="rtsp://localhost:8554" \
  python -m uvicorn main:app --host 0.0.0.0 --port 9100
```

## Add a stream at runtime

```bash
curl -X POST http://localhost:9100/streams \
  -H "Content-Type: application/json" \
  -d '{"stream_id": "cam2", "url": "rtsp://..."}'
```

The browser can then play it via the WHEP path `http://<host>:8889/cam2/whep`.
