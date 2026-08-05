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
| `RTSP_TIMEOUT`         | `15.0`                  | PyAV open/read timeout (seconds)                   |
| `WEBRTC_AUTO_PUBLISH`  | `true`                  | Relay each source into MediaMTX                    |
| `WEBRTC_RELAY_URL`     | `rtsp://mediamtx:8554`  | MediaMTX RTSP base the relay publishes to          |
| `WEBRTC_SIGNALING_URL` | *(derived)*             | Public WHEP base for the browser (host:port)       |
| `WEBRTC_SIGNALING_PORT`| `8889`                  | MediaMTX WebRTC port                               |
| `MAX_STREAMS`          | `8`                     | Maximum concurrent streams                         |


## Download the Vision Language Model (one-time, ~5 min)

This downloads the AI model that powers the captions/alerts. It only needs to run once. The model parameter is configurable and the user is requested to confirm the license agreement before the download.

```bash
./model_download_scripts/download_models.sh \
  --model OpenGVLab/InternVL2-1B \
  --type vlm \
  --weight-format int8
```

### Specifying the conversion device

By default, the model is converted to run on CPU. To explicitly run on other device:

```bash
# Specify your desired target device in the --device flag
./model_download_scripts/download_models.sh \
  --model OpenGVLab/InternVL2-1B \
  --type vlm \
  --weight-format int8 \
  --device <CPU|GPU|NPU>
```
> Note: NPU currently requires `int4` quantization for VLM conversion. If you pass `--device NPU` with `int8` or `fp16`, the script automatically overrides it to `int4`.

## Run with Docker Compose

```bash
bash scripts/setup_env.sh
docker compose up -d --build
```

Open http://localhost:9100, then add a stream URL from the UI
(or call `POST /streams` with `{ "url": "rtsp://...", "stream_id": "default" }`).

> `bash scripts/setup_env.sh` creates `.env` from `.env.example`, detects the
> host LAN IP, and sets the WebRTC signaling URL so browsers on other machines
> can reach MediaMTX and coturn. Use `--force` to regenerate `.env`.
