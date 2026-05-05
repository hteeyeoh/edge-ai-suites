# Embedding and RAG Chatbot Setup

Live Video Captioning includes additional capabilities that allow users to generate embeddings from video frames and caption text, then store them in a vector database. A RAG-based chatbot can connect to this vector database to retrieve relevant context and respond to user queries. The RAG chatbot can be deployed either on the same host as the Live Video Captioning service or on a separate host, depending on the user’s setup preferences.

## Enabling Embedding Creation and RAG Chatbot

User can enable embedding creation and RAG Chatbot by following the steps below. This deploy all the application containers under single host.

1. Build the chatbot application from [open-edge-ai-libraries forked repo](https://github.com/hteeyeoh/edge-ai-libraries/tree/chatqna-vdms-clean).

   ```bash
   # clone repo
   git clone <forked-repo-above> && git checkout remotes/origin/chatqna-vdms-clean && cd edge-ai-libraries

   # checkout branch
   git checkout remotes/origin/chatqna-vdms-clean -b chatqna-vdms

   # Navigate to the directory
   cd sample-applications/chat-question-and-answer-core

   # Build the backend
   docker build -t chatqna:latest -f docker/Dockerfile .

   # Build the frontend UI
   cd ui/
   docker build -t chatqna-ui:latest .
   ```

2. Navigate to the open-edge-ai-suites code directory. Include `ENABLE_EMBEDDING` and set to `True` in the .env file under `edge-ai-suites/metro-ai-suite/live-video-analysis/live-video-captioning` .

   ```bash
   WHIP_SERVER_IP=mediamtx
   WHIP_SERVER_PORT=8889
   WHIP_SERVER_TIMEOUT=30s
   PROJECT_NAME=live-captioning
   HOST_IP=<YOUR_HOST_IP>
   EVAM_HOST_PORT=8040
   EVAM_PORT=8080
   DASHBOARD_PORT=4173
   WEBRTC_PEER_ID=stream
   WEBRTC_BITRATE=5000
   ENABLE_EMBEDDING=True  # Enable embedding creation
   ```
3. Export the following environment variables.

   ```bash
   export HUGGINGFACEHUB_API_TOKEN=<your-huggingface-token>
   export EMBEDDING_MODEL_NAME=QwenText/qwen3-embedding-0.6b   # <- You may change to your desired embedding mode. Default using this text embedding model,
   export VDMS_HOST="vdms-vector-db"
   export VDMS_PORT=55555
   export VDMS_EMBEDDING_MODEL=${EMBEDDING_MODEL_NAME}
   export VDMS_EMBEDDING_HOST="multimodal-embedding-serving"
   export VDMS_EMBEDDING_HOST_PORT=8000
   export EMBEDDING_OV_MODELS_DIR=/app/ov_models
   export EMBEDDING_SERVER_PORT=9777
   export EMBEDDING_DEVICE=GPU
   export EMBEDDING_USE_OV=true
   export COMPOSE_PROFILES=EMBEDDING
   export VDMS_VDB_HOST_PORT=55555
   export VDMS_VDB_HOST=vdms-vector-db

   # [OPTIONAL] To run in OFFLINE mode.
   # Please be aware that not to run offline mode if first run. Model files need to be downloaded and store in system before they can run in offline mode.
   # Export the following:
   export OFFLINE_MODE=True
    
   ```

4. Run the following setup scripts.

   ```bash
   # Navigate to `edge-ai-suites/metro-ai-suite/live-video-analysis/live-video-captioning`.
   source rag_setup_scripts/setup.sh
   source rag_setup_scripts/setup_env.sh -v vdms
   ```

5. Now, start the application using Docker Compose tool:

   ```bash
   docker compose -f compose.yaml -f compose.rag.yaml up
   ```

6. Make sure that all the application containers are up and in `healthy` state before access the application.

   ```bash
   # Some container might take some additional time to get read. Make sure you see them in healthy state using `docker ps` command.
   docker ps
   ```

7. Access the application:

   To start processing video with live captioning:

    1. Open the dashboard at `http://<HOST_IP>:4173`.
    2. Enter an RTSP URL for your video stream.
    3. Select a VLM model from the dropdown.
    4. Customize the prompt and maximum tokens as needed.
    5. Click **Start** to begin captioning.

   Note: If running in a proxy network, ensure that your RTSP stream URLs or IPs are added to the no_proxy environment variable to allow direct connections to the stream source without going through the proxy.

   To access the RAG Chatbot:

    1. Open the chatbot at `http://<HOST_IP>:8102`.
    2. Type in your query and get the response.

8. Stop the application using below:

   ```bash
   docker compose -f compose.yaml -f compose.rag.yaml down
   ```

## Troubleshooting

Please refer to the [Get Started](./get-started.md) if anything wrong with the Live Captioning Sample. It is encourages to go through the get-started guide before trying this embedding and rag setup.

## Next Steps

- [Get Started](./get-started.md) - Basic setup and configuration
- [API Reference](./api-reference.md) - REST API documentation
- [System Requirements](./system-requirements.md) - Hardware and software requirements
