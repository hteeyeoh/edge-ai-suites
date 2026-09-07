/* Copyright (C) 2026 Intel Corporation */
/* SPDX-License-Identifier: Apache-2.0 */

/**
 * Streams feature controller.
 * Owns stream CRUD UX, card lifecycle, polling, and playback orchestration.
 */

import { addStream, deleteStream, fetchStreams } from "../../services/api.js";
import { startWhepPlayback } from "../../services/webrtc.js";
import {
    formatAlertEventDetails,
    formatVlmMetrics,
    isAlertDetected,
    sanitizeStreamId,
} from "../../utils/stream.js";

const POLL_INTERVAL_MS = 2000;

export function createStreamsController(elements, { openAlertDrawer }) {
    const {
        statusPill,
        rtspForm,
        rtspInput,
        streamIdInput,
        alertEventInput,
        rtspSubmit,
        rtspMessage,
        streamsGrid,
        streamsEmpty,
    } = elements;

    const players = new Map();
    let autoStreamCounter = 1;

    function nextStreamId() {
        const id = `stream-${Date.now()}-${autoStreamCounter}`;
        autoStreamCounter += 1;
        return id;
    }

    function setStatus(text, kind) {
        statusPill.textContent = text;
        statusPill.className = `pill pill--${kind}`;
    }

    function setRtspMessage(message, isError = false) {
        rtspMessage.textContent = message;
        rtspMessage.style.color = isError ? "var(--err)" : "var(--muted)";
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
                const connState = player.playback?.pc.connectionState;
                if (connState === "connected") {
                    setCardOverlay(player, "", false);
                }
                if (connState === "failed" || connState === "disconnected" || connState === "closed") {
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
            await deleteStream(streamId);
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

        const alertBell = document.createElement("button");
        alertBell.className = "stream-alert-bell";
        alertBell.type = "button";
        alertBell.innerHTML = '\uD83D\uDD14<span class="stream-alert-bell__badge" hidden>0</span>';
        alertBell.setAttribute("aria-label", "View alert history");

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

        const vlmMetrics = document.createElement("div");
        vlmMetrics.className = "stream-vlm-metrics";
        vlmMetrics.textContent = "TTFT: - ms | TPOT: - ms | Throughput: - tok/s";

        frame.appendChild(video);
        frame.appendChild(overlay);
        head.appendChild(idText);
        actions.appendChild(alertBell);
        actions.appendChild(stopButton);
        head.appendChild(actions);
        card.appendChild(head);
        card.appendChild(meta);
        card.appendChild(alertDetails);
        card.appendChild(frame);
        card.appendChild(vlmMetrics);
        streamsGrid.appendChild(card);

        const player = {
            stream,
            card,
            video,
            overlay,
            meta,
            alertDetails,
            alertBell,
            alertCountSeen: null,
            alertPulseTimeout: null,
            vlmMetrics,
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
        alertBell.addEventListener("click", () => {
            alertBell.classList.remove("stream-alert-bell--pulse");
            if (player.alertPulseTimeout) {
                clearTimeout(player.alertPulseTimeout);
                player.alertPulseTimeout = null;
            }
            openAlertDrawer(stream.stream_id);
        });

        return player;
    }

    function syncPlayerCard(player, stream) {
        player.stream = stream;
        player.meta.textContent = stream.url || "";

        if (player.alertDetails) {
            const hasAlertEvent = Boolean((stream.alert_event || "").trim());
            player.alertDetails.textContent = formatAlertEventDetails(stream);
            player.alertDetails.classList.toggle("stream-alert-details--hidden", !hasAlertEvent);
        }

        const alertDetected = isAlertDetected(stream.caption);
        player.card.classList.toggle("stream-card--alert", alertDetected);

        if (player.vlmMetrics) {
            player.vlmMetrics.textContent = formatVlmMetrics(stream);
        }

        if (player.alertBell) {
            const count = Number(stream.alert_count) || 0;
            const badge = player.alertBell.querySelector(".stream-alert-bell__badge");
            player.alertBell.classList.toggle("stream-alert-bell--visible", count > 0);

            if (badge) {
                badge.hidden = count === 0;
                badge.textContent = count > 99 ? "99+" : String(count);
            }

            // Only pulse for a genuine increase after the baseline is known, not on first load.
            if (player.alertCountSeen !== null && count > player.alertCountSeen) {
                player.alertBell.classList.remove("stream-alert-bell--pulse");
                void player.alertBell.offsetWidth; // restart animation if already pulsing
                player.alertBell.classList.add("stream-alert-bell--pulse");
                if (player.alertPulseTimeout) clearTimeout(player.alertPulseTimeout);
                player.alertPulseTimeout = setTimeout(() => {
                    player.alertBell.classList.remove("stream-alert-bell--pulse");
                    player.alertPulseTimeout = null;
                }, 2400);
            }
            player.alertCountSeen = count;
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
            const streams = await fetchStreams();
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

    async function onSubmitRtsp(event) {
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

        if (/[,;|/]/.test(alertEvent)) {
            setRtspMessage("Only one alert event is supported per stream.", true);
            return;
        }

        const customId = sanitizeStreamId(streamIdInput.value || "");
        const streamId = customId || nextStreamId();

        rtspSubmit.disabled = true;
        setRtspMessage(`Adding stream '${streamId}'...`);

        try {
            await addStream({ streamId, url, alertEvent });

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
    }

    function init() {
        rtspForm.addEventListener("submit", onSubmitRtsp);
        pollHealth();
        setInterval(pollHealth, POLL_INTERVAL_MS);
    }

    return {
        init,
        pollHealth,
    };
}
