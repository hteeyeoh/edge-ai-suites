/* Copyright (C) 2026 Intel Corporation */
/* SPDX-License-Identifier: Apache-2.0 */

const statusPill = document.getElementById("status-pill");
const rtspForm = document.getElementById("rtsp-form");
const rtspInput = document.getElementById("rtsp-url");
const streamIdInput = document.getElementById("stream-id");
const alertEventInput = document.getElementById("alert-event");
const rtspSubmit = document.getElementById("rtsp-submit");
const rtspMessage = document.getElementById("rtsp-message");
const streamsGrid = document.getElementById("streams-grid");
const streamsEmpty = document.getElementById("streams-empty");

const metricsConnection = document.getElementById("metrics-connection");
const cpuVal = document.getElementById("cpu-val");
const ramVal = document.getElementById("ram-val");
const gpuVal = document.getElementById("gpu-val");
const npuVal = document.getElementById("npu-val");
const metricsCanvas = document.getElementById("metrics-canvas");

const players = new Map();
let autoStreamCounter = 1;

let metricsSource = null;
let metricsReconnectTimer = null;
let metricsReconnectAttempts = 0;
const metricsMaxReconnectAttempts = 10;
const metricsHistory = [];
const metricsHistoryLimit = 60;

function setStatus(text, kind) {
    statusPill.textContent = text;
    statusPill.className = `pill pill--${kind}`;
}

function setRtspMessage(message, isError = false) {
    rtspMessage.textContent = message;
    rtspMessage.style.color = isError ? "var(--err)" : "var(--muted)";
}

function resolveWebRtcBase() {
    const cfg = window.RUNTIME_CONFIG || {};
    const configured = (cfg.webrtcSignalingUrl || "").trim();
    if (configured) {
        return configured.replace(/\/+$/, "");
    }
    const protocol = window.location.protocol === "https:" ? "https:" : "http:";
    const host = window.location.hostname;
    const port = String(cfg.webrtcSignalingPort || 8889);
    return `${protocol}//${host}:${port}`;
}

function whepUrl(streamId) {
    return `${resolveWebRtcBase()}/${encodeURIComponent(streamId)}/whep`;
}

function waitForIceGathering(pc, timeoutMs = 2500) {
    if (pc.iceGatheringState === "complete") return Promise.resolve();
    return new Promise((resolve) => {
        let done = false;
        const finish = () => {
            if (done) return;
            done = true;
            pc.removeEventListener("icegatheringstatechange", onChange);
            resolve();
        };
        const onChange = () => {
            if (pc.iceGatheringState === "complete") finish();
        };
        pc.addEventListener("icegatheringstatechange", onChange);
        setTimeout(finish, timeoutMs);
    });
}

async function startWhepPlayback(streamId, videoElement) {
    const pc = new RTCPeerConnection();
    let sessionUrl = "";

    pc.addTransceiver("video", { direction: "recvonly" });
    pc.ontrack = (event) => {
        const stream = event.streams && event.streams[0];
        if (!stream) return;
        videoElement.srcObject = stream;
        videoElement.play().catch(() => { /* autoplay may be blocked */ });
    };

    await pc.setLocalDescription(await pc.createOffer());
    await waitForIceGathering(pc);

    const resp = await fetch(whepUrl(streamId), {
        method: "POST",
        headers: { "Content-Type": "application/sdp" },
        body: pc.localDescription?.sdp || "",
    });
    if (!resp.ok) {
        pc.close();
        throw new Error(`WHEP handshake failed: HTTP ${resp.status}`);
    }

    const location = resp.headers.get("Location") || resp.headers.get("location");
    if (location) {
        sessionUrl = new URL(location, whepUrl(streamId)).toString();
    }

    const answer = await resp.text();
    await pc.setRemoteDescription({ type: "answer", sdp: answer });

    const cleanup = () => {
        try { pc.close(); } catch (_err) { /* ignore */ }
        if (sessionUrl) {
            fetch(sessionUrl, { method: "DELETE" }).catch(() => { /* best effort */ });
        }
    };
    return { pc, cleanup };
}

function updateStatusFromPlayers() {
    const values = Array.from(players.values());
    const liveCount = values.filter((p) => p.stream?.publishing && p.playback).length;
    const total = values.length;

    if (total === 0) {
        setStatus("no stream", "idle");
        return;
    }
    if (liveCount > 0) {
        setStatus(`${liveCount}/${total} live`, "ok");
        return;
    }
    setStatus("waiting for source", "idle");
}

function setCardOverlay(player, text, show = true) {
    player.overlay.textContent = text;
    player.overlay.style.display = show ? "flex" : "none";
}

function teardownPlayer(streamId, message = "Reconnecting...") {
    const player = players.get(streamId);
    if (!player) return;

    if (player.playback) {
        player.playback.cleanup();
        player.playback = null;
    }
    player.video.srcObject = null;
    setCardOverlay(player, message, true);
}

async function connectPlayer(streamId) {
    const player = players.get(streamId);
    if (!player || player.connecting || player.playback) return;
    if (!player.stream?.publishing) {
        setCardOverlay(player, "Waiting for publisher...", true);
        return;
    }

    player.connecting = true;
    setCardOverlay(player, "Connecting...", true);
    try {
        player.playback = await startWhepPlayback(streamId, player.video);
        player.playback.pc.addEventListener("connectionstatechange", () => {
            const state = player.playback?.pc.connectionState;
            if (state === "connected") {
                setCardOverlay(player, "", false);
            }
            if (state === "failed" || state === "disconnected" || state === "closed") {
                teardownPlayer(streamId);
            }
            updateStatusFromPlayers();
        });
    } catch (_err) {
        teardownPlayer(streamId, "Unable to connect. Retrying...");
    } finally {
        player.connecting = false;
        updateStatusFromPlayers();
    }
}

function removeCard(streamId) {
    const player = players.get(streamId);
    if (!player) return;
    teardownPlayer(streamId, "Stopped");
    player.card.remove();
    players.delete(streamId);
    streamsEmpty.style.display = players.size === 0 ? "block" : "none";
    updateStatusFromPlayers();
}

async function stopStream(streamId, button) {
    button.disabled = true;
    try {
        const resp = await fetch(`/streams/${encodeURIComponent(streamId)}`, { method: "DELETE" });
        if (!resp.ok && resp.status !== 404) {
            let detail = "Failed to stop stream.";
            try {
                const payload = await resp.json();
                if (payload?.detail) detail = String(payload.detail);
            } catch (_err) {
                // keep default message
            }
            throw new Error(detail);
        }
        removeCard(streamId);
        setRtspMessage(`Stopped stream '${streamId}'.`);
    } catch (err) {
        const message = err instanceof Error ? err.message : "Failed to stop stream.";
        setRtspMessage(message, true);
        button.disabled = false;
    }
}

function createCard(stream) {
    const card = document.createElement("article");
    card.className = "stream-card";

    const head = document.createElement("div");
    head.className = "stream-head";

    const idText = document.createElement("h3");
    idText.className = "stream-id";
    idText.textContent = stream.stream_id;

    const stopButton = document.createElement("button");
    stopButton.className = "stream-stop";
    stopButton.type = "button";
    stopButton.textContent = "Stop";

    const actions = document.createElement("div");
    actions.className = "stream-actions";

    const infoButton = document.createElement("button");
    infoButton.className = "stream-info-btn";
    infoButton.type = "button";
    infoButton.textContent = "i";
    infoButton.setAttribute("aria-label", "Show alert event details");

    const alertDetails = document.createElement("p");
    alertDetails.className = "stream-alert-details stream-alert-details--hidden";

    const meta = document.createElement("p");
    meta.className = "stream-meta";

    const frame = document.createElement("div");
    frame.className = "stream-frame";
    const video = document.createElement("video");
    video.autoplay = true;
    video.muted = true;
    video.playsInline = true;
    const overlay = document.createElement("div");
    overlay.className = "stream-overlay";
    overlay.textContent = "Waiting for publisher...";

    const caption = document.createElement("p");
    caption.className = "stream-caption";
    caption.textContent = "Awaiting scene description…";

    const vlmMetrics = document.createElement("div");
    vlmMetrics.className = "stream-vlm-metrics";
    vlmMetrics.textContent = "TTFT: - ms | TPOT: - ms | Throughput: - tok/s";

    frame.appendChild(video);
    frame.appendChild(overlay);
    head.appendChild(idText);
    actions.appendChild(infoButton);
    actions.appendChild(stopButton);
    head.appendChild(actions);
    card.appendChild(head);
    card.appendChild(meta);
    card.appendChild(alertDetails);
    card.appendChild(frame);
    card.appendChild(vlmMetrics);
    card.appendChild(caption);
    streamsGrid.appendChild(card);

    const player = {
        stream,
        card,
        video,
        overlay,
        meta,
        alertDetails,
        infoButton,
        vlmMetrics,
        caption,
        stopButton,
        playback: null,
        connecting: false,
    };
    players.set(stream.stream_id, player);

    video.addEventListener("loadeddata", () => {
        setCardOverlay(player, "", false);
        updateStatusFromPlayers();
    });
    stopButton.addEventListener("click", () => {
        stopStream(stream.stream_id, stopButton);
    });
    infoButton.addEventListener("click", () => {
        const willShow = player.alertDetails.classList.contains("stream-alert-details--hidden");
        player.alertDetails.classList.toggle("stream-alert-details--hidden", !willShow);
        infoButton.classList.toggle("stream-info-btn--active", willShow);
        infoButton.setAttribute("aria-expanded", String(willShow));
    });

    return player;
}

function formatAlertEventDetails(stream) {
    const eventName = (stream.alert_event || "").trim();
    return eventName
        ? `Alert Event: ${eventName}`
        : "Alert Event: not provided";
}

function isAlertDetected(captionText) {
    const normalized = String(captionText || "").trim().toLowerCase();
    if (!normalized) return false;
    // VLM is configured for binary responses (Yes/No), so treat Yes as alert.
    return /^yes\b/.test(normalized);
}

function formatMetricNumber(value, digits = 1) {
    if (value === null || value === undefined || value === "") return "-";
    const num = Number(value);
    if (!Number.isFinite(num)) return "-";
    return num.toFixed(digits);
}

function formatVlmMetrics(stream) {
    const ttft = formatMetricNumber(stream.ttft_ms);
    const tpot = formatMetricNumber(stream.tpot_ms);
    const throughput = formatMetricNumber(stream.throughput_tps);
    return `TTFT: ${ttft} ms | TPOT: ${tpot} ms | Throughput: ${throughput} tok/s`;
}

function syncPlayerCard(player, stream) {
    player.stream = stream;
    player.meta.textContent = stream.url || "";
    if (player.alertDetails) {
        player.alertDetails.textContent = formatAlertEventDetails(stream);
    }
    if (player.infoButton) {
        const hasAlertEvent = Boolean((stream.alert_event || "").trim());
        player.infoButton.disabled = !hasAlertEvent;
        player.infoButton.title = hasAlertEvent
            ? "Show alert event details"
            : "No alert event details";
        if (!hasAlertEvent) {
            player.alertDetails.classList.add("stream-alert-details--hidden");
            player.infoButton.classList.remove("stream-info-btn--active");
            player.infoButton.setAttribute("aria-expanded", "false");
        }
    }
    if (player.caption) {
        player.caption.textContent = stream.caption || "Awaiting scene description…";
        player.caption.classList.toggle("stream-caption--active", Boolean(stream.caption));
        const alertDetected = isAlertDetected(stream.caption);
        player.card.classList.toggle("stream-card--alert", alertDetected);
        player.caption.classList.toggle("stream-caption--alert", alertDetected);
    }
    if (player.vlmMetrics) {
        player.vlmMetrics.textContent = formatVlmMetrics(stream);
    }
    if (stream.publishing) {
        if (!player.playback) {
            setCardOverlay(player, "Connecting...", true);
            connectPlayer(stream.stream_id);
        }
    } else if (!player.playback && !player.connecting) {
        setCardOverlay(player, "Waiting for publisher...", true);
    }
}

async function pollHealth() {
    try {
        const res = await fetch("/streams");
        const data = await res.json();
        const streams = Array.isArray(data.streams) ? data.streams : [];

        const seen = new Set();
        streams.forEach((stream) => {
            seen.add(stream.stream_id);
            let player = players.get(stream.stream_id);
            if (!player) {
                player = createCard(stream);
            }
            syncPlayerCard(player, stream);
        });

        Array.from(players.keys()).forEach((streamId) => {
            if (!seen.has(streamId)) {
                removeCard(streamId);
            }
        });

        streamsEmpty.style.display = players.size === 0 ? "block" : "none";
        updateStatusFromPlayers();
    } catch (_err) {
        setStatus("offline", "err");
        setRtspMessage("Unable to fetch stream status.", true);
    }
}

function sanitizeStreamId(value) {
    return value
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9_-]+/g, "-")
        .replace(/^-+|-+$/g, "")
        .slice(0, 48);
}

function nextStreamId() {
    const id = `stream-${Date.now()}-${autoStreamCounter}`;
    autoStreamCounter += 1;
    return id;
}

function getMetricsStreamUrl() {
    const cfg = window.RUNTIME_CONFIG || {};
    if (window.METRICS_SERVICE_URL) {
        return window.METRICS_SERVICE_URL;
    }
    const protocol = window.location.protocol === "https:" ? "https:" : "http:";
    const host = window.location.hostname;
    const port = cfg.metricsServicePort || window.METRICS_SERVICE_PORT || "9090";
    return `${protocol}//${host}:${port}/metrics/stream`;
}

function setMetricsConnection(connected) {
    metricsConnection.textContent = connected ? "connected" : "disconnected";
    metricsConnection.className = connected
        ? "metrics-connection metrics-connection--ok"
        : "metrics-connection metrics-connection--idle";
}

function updateMetricValue(el, value) {
    el.textContent = value === null ? "-" : `${value.toFixed(1)}%`;
}

function drawMetricLine(ctx, samples, key, color, left, right, top, bottom) {
    if (samples.length < 2) return;
    const xStep = (right - left) / Math.max(samples.length - 1, 1);
    let started = false;
    ctx.beginPath();
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    samples.forEach((sample, index) => {
        const value = sample[key];
        if (value === null || value === undefined) return;
        const x = left + index * xStep;
        const y = bottom - (Math.max(0, Math.min(100, value)) / 100) * (bottom - top);
        if (started) {
            ctx.lineTo(x, y);
        } else {
            ctx.moveTo(x, y);
            started = true;
        }
    });
    if (started) {
        ctx.stroke();
    }
}

function drawMetricsChart() {
    if (!metricsCanvas) return;
    const ctx = metricsCanvas.getContext("2d");
    if (!ctx) return;

    const cssWidth = metricsCanvas.clientWidth || 640;
    const cssHeight = 220;
    if (metricsCanvas.width !== cssWidth) metricsCanvas.width = cssWidth;
    if (metricsCanvas.height !== cssHeight) metricsCanvas.height = cssHeight;

    const width = metricsCanvas.width;
    const height = metricsCanvas.height;
    const leftPadding = 38;
    const rightPadding = 10;
    const topPadding = 12;
    const bottomPadding = 20;
    const plotLeft = leftPadding;
    const plotRight = width - rightPadding;
    const plotTop = topPadding;
    const plotBottom = height - bottomPadding;

    ctx.clearRect(0, 0, width, height);

    // Y-axis labels and horizontal grid (0-100%).
    ctx.font = "11px 'Segoe UI', sans-serif";
    ctx.fillStyle = "rgba(139, 148, 158, 0.9)";
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    ctx.strokeStyle = "rgba(139, 148, 158, 0.2)";
    ctx.lineWidth = 1;
    for (let pct = 0; pct <= 100; pct += 10) {
        const y = plotBottom - (pct / 100) * (plotBottom - plotTop);
        ctx.beginPath();
        ctx.moveTo(plotLeft, y);
        ctx.lineTo(plotRight, y);
        ctx.stroke();
        ctx.fillText(String(pct), plotLeft - 6, y);
    }

    // Y-axis line.
    ctx.beginPath();
    ctx.moveTo(plotLeft, plotTop);
    ctx.lineTo(plotLeft, plotBottom);
    ctx.strokeStyle = "rgba(139, 148, 158, 0.28)";
    ctx.stroke();

    drawMetricLine(ctx, metricsHistory, "cpu", "#1ad0ff", plotLeft, plotRight, plotTop, plotBottom);
    drawMetricLine(ctx, metricsHistory, "ram", "#8ca0c2", plotLeft, plotRight, plotTop, plotBottom);
    drawMetricLine(ctx, metricsHistory, "gpu", "#ffb347", plotLeft, plotRight, plotTop, plotBottom);
    drawMetricLine(ctx, metricsHistory, "npu", "#b388ff", plotLeft, plotRight, plotTop, plotBottom);
}

function processMetrics(metrics) {
    let cpu = null;
    let ram = null;
    let npu = null;
    const gpuByDevice = new Map();

    function gpuDeviceId(labels) {
        if (labels.gpu_id !== undefined && labels.gpu_id !== null) return String(labels.gpu_id);
        if (labels.device !== undefined && labels.device !== null) return String(labels.device);
        if (labels.card !== undefined && labels.card !== null) return String(labels.card);
        return "0";
    }

    metrics.forEach((metric) => {
        const labels = metric.labels || {};
        if (metric.name === "cpu_usage_user") {
            if (labels.cpu === undefined || labels.cpu === "cpu-total") {
                cpu = metric.value;
            }
        } else if (metric.name === "mem_used_percent") {
            ram = metric.value;
        } else if (metric.name === "gpu_engine_usage_usage") {
            if (!labels.engine) return;
            const id = gpuDeviceId(labels);
            const prev = gpuByDevice.get(id);
            const next = prev === undefined ? metric.value : Math.max(prev, metric.value);
            gpuByDevice.set(id, next);
        } else if (metric.name === "npu_utilization") {
            npu = metric.value;
        }
    });

    let gpu = null;
    if (gpuByDevice.size > 0) {
        gpu = Math.max(...Array.from(gpuByDevice.values()));
    }

    updateMetricValue(cpuVal, cpu);
    updateMetricValue(ramVal, ram);
    updateMetricValue(gpuVal, gpu);
    updateMetricValue(npuVal, npu);

    if (cpu !== null || ram !== null || gpu !== null || npu !== null) {
        metricsHistory.push({
            cpu,
            ram,
            gpu,
            npu,
        });
        while (metricsHistory.length > metricsHistoryLimit) {
            metricsHistory.shift();
        }
        drawMetricsChart();
    }
}

function connectMetricsStream() {
    if (metricsSource && metricsSource.readyState !== EventSource.CLOSED) {
        return;
    }

    const streamUrl = getMetricsStreamUrl();
    metricsSource = new EventSource(streamUrl);

    metricsSource.onopen = () => {
        metricsReconnectAttempts = 0;
        setMetricsConnection(true);
    };

    metricsSource.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (!Array.isArray(data.metrics)) return;
            processMetrics(data.metrics);
        } catch (_err) {
            // Ignore malformed payload frames.
        }
    };

    metricsSource.onerror = () => {
        setMetricsConnection(false);
        if (metricsSource && metricsSource.readyState === EventSource.CLOSED) {
            metricsSource.close();
            metricsSource = null;
            if (metricsReconnectAttempts < metricsMaxReconnectAttempts) {
                metricsReconnectAttempts += 1;
                metricsReconnectTimer = setTimeout(connectMetricsStream, 3000);
            }
        }
    };
}

rtspForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const url = rtspInput.value.trim();
    if (!url) {
        setRtspMessage("Please enter an RTSP URL.", true);
        return;
    }
    const alertEvent = (alertEventInput?.value || "").trim();
    if (!alertEvent) {
        setRtspMessage("Please enter an alert event.", true);
        return;
    }

    const customId = sanitizeStreamId(streamIdInput.value || "");
    const streamId = customId || nextStreamId();

    rtspSubmit.disabled = true;
    setRtspMessage(`Adding stream '${streamId}'...`);

    try {
        const addResp = await fetch("/streams", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ stream_id: streamId, url, alert_event: alertEvent }),
        });

        if (!addResp.ok) {
            let detail = "Failed to add stream.";
            try {
                const payload = await addResp.json();
                if (payload?.detail) detail = String(payload.detail);
            } catch (_err) {
                // keep default message
            }
            throw new Error(detail);
        }

        setRtspMessage(`Stream '${streamId}' added.`);
        rtspInput.value = "";
        streamIdInput.value = "";
        if (alertEventInput) {
            alertEventInput.value = "";
        }
        await pollHealth();
    } catch (err) {
        const message = err instanceof Error ? err.message : "Failed to add stream.";
        setRtspMessage(message, true);
    } finally {
        rtspSubmit.disabled = false;
    }
});

window.addEventListener("resize", drawMetricsChart);
window.addEventListener("beforeunload", () => {
    if (metricsReconnectTimer) {
        clearTimeout(metricsReconnectTimer);
    }
    if (metricsSource) {
        metricsSource.close();
    }
});

drawMetricsChart();
connectMetricsStream();
pollHealth();
setInterval(pollHealth, 2000);