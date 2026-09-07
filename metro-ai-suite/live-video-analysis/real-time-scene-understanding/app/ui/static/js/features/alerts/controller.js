/* Copyright (C) 2026 Intel Corporation */
/* SPDX-License-Identifier: Apache-2.0 */

/**
 * Alerts feature controller.
 * Manages alert history drawer, pagination, and alert detail modal flows.
 */

import { fetchAlertDetail, fetchAlertPage } from "../../services/api.js";

export function createAlertsController(elements) {
    const state = { streamId: null, offset: 0, total: 0 };

    const {
        alertDrawer,
        alertDrawerStream,
        alertDrawerList,
        alertDrawerStatus,
        alertDrawerMore,
        alertDrawerClose,
        alertModal,
        alertModalVideo,
        alertModalMeta,
        alertModalSummary,
        alertModalStatus,
        alertModalClose,
    } = elements;

    function closeAlertModal() {
        if (!alertModal) return;
        alertModal.classList.add("alert-modal--hidden");
        alertModal.setAttribute("aria-hidden", "true");
        if (alertModalVideo) {
            alertModalVideo.pause();
            alertModalVideo.removeAttribute("src");
            alertModalVideo.load();
        }
    }

    async function openAlertDetail(streamId, frameId) {
        if (!alertModal) return;

        alertModal.classList.remove("alert-modal--hidden");
        alertModal.setAttribute("aria-hidden", "false");
        if (alertModalStatus) alertModalStatus.textContent = "Loading alert...";
        if (alertModalSummary) alertModalSummary.textContent = "No summary yet.";
        if (alertModalMeta) alertModalMeta.innerHTML = "";

        try {
            const data = await fetchAlertDetail(streamId, frameId);

            if (alertModalVideo && data.video_url) {
                alertModalVideo.src = data.video_url;
                alertModalVideo.load();
            }

            const entries = [
                ["Stream", data.stream_id || streamId],
                ["Alert", data.alert_event || "-"],
                ["Uploaded", data.uploaded_at ? new Date(data.uploaded_at).toLocaleString() : "-"],
                ["Frame", data.frame_id || "-"],
            ];

            if (alertModalMeta) {
                entries.forEach(([key, value]) => {
                    const row = document.createElement("div");
                    row.className = "alert-modal__meta-row";
                    const term = document.createElement("dt");
                    term.textContent = key;
                    const definition = document.createElement("dd");
                    definition.textContent = String(value || "-");
                    row.appendChild(term);
                    row.appendChild(definition);
                    alertModalMeta.appendChild(row);
                });
            }

            if (alertModalSummary) {
                alertModalSummary.textContent = data.description || "No deep analyzer summary available yet.";
            }
            if (alertModalStatus) alertModalStatus.textContent = "";
        } catch (_err) {
            if (alertModalStatus) {
                alertModalStatus.textContent = "Failed to load this alert. It may still be processing.";
            }
        }
    }

    function renderAlertRows(alerts, streamId) {
        alerts.forEach((alert) => {
            const li = document.createElement("li");
            const button = document.createElement("button");
            button.type = "button";
            button.className = "alert-drawer__row";

            const time = alert.uploaded_at ? new Date(alert.uploaded_at).toLocaleString() : "Unknown time";
            const timeText = document.createElement("span");
            timeText.className = "alert-drawer__row-time";
            timeText.textContent = time;

            const eventText = document.createElement("span");
            eventText.className = "alert-drawer__row-event";
            eventText.textContent = alert.alert_event || "Alert";

            const thumbnailUrl = String(alert.thumbnail_url || "").trim();
            const captionText = document.createElement("span");
            captionText.className = "alert-drawer__row-caption";
            captionText.textContent = alert.trigger_caption || "Preview unavailable";

            button.appendChild(timeText);
            button.appendChild(eventText);

            if (thumbnailUrl) {
                const thumb = document.createElement("img");
                thumb.className = "alert-drawer__row-thumb";
                thumb.alt = `Trigger frame for ${alert.alert_event || "alert"}`;
                thumb.loading = "lazy";
                thumb.src = thumbnailUrl;
                thumb.addEventListener("error", () => {
                    thumb.remove();
                    if (!button.contains(captionText)) {
                        button.appendChild(captionText);
                    }
                });
                button.appendChild(thumb);
            } else {
                button.appendChild(captionText);
            }

            button.addEventListener("click", () => openAlertDetail(streamId, alert.frame_id));
            li.appendChild(button);
            alertDrawerList.appendChild(li);
        });
    }

    async function loadAlertPage(streamId) {
        const data = await fetchAlertPage(streamId, state.offset);
        const alerts = data.alerts;

        state.total = data.total;
        state.offset += alerts.length;

        renderAlertRows(alerts, streamId);

        if (state.offset === 0) {
            alertDrawerStatus.textContent = "No alerts recorded yet for this stream.";
        } else {
            alertDrawerStatus.textContent = "";
        }
        alertDrawerMore.hidden = state.offset >= state.total;
    }

    function closeAlertDrawer() {
        if (!alertDrawer) return;
        alertDrawer.classList.add("alert-drawer--hidden");
        alertDrawer.setAttribute("aria-hidden", "true");
        state.streamId = null;
        state.offset = 0;
        state.total = 0;
    }

    async function openAlertDrawer(streamId) {
        if (!alertDrawer) return;

        alertDrawer.classList.remove("alert-drawer--hidden");
        alertDrawer.setAttribute("aria-hidden", "false");
        alertDrawerStream.textContent = streamId;
        alertDrawerList.innerHTML = "";
        alertDrawerMore.hidden = true;
        alertDrawerStatus.textContent = "Loading alerts...";

        state.streamId = streamId;
        state.offset = 0;
        state.total = 0;

        try {
            await loadAlertPage(streamId);
        } catch (_err) {
            alertDrawerStatus.textContent = "Unable to load alert history.";
        }
    }

    function bindEvents() {
        if (alertDrawerClose) alertDrawerClose.addEventListener("click", closeAlertDrawer);

        if (alertDrawer) {
            alertDrawer.addEventListener("click", (event) => {
                if (event.target?.dataset?.close === "alert-drawer") closeAlertDrawer();
            });
        }

        if (alertDrawerMore) {
            alertDrawerMore.addEventListener("click", async () => {
                if (!state.streamId) return;
                try {
                    await loadAlertPage(state.streamId);
                } catch (_err) {
                    alertDrawerStatus.textContent = "Unable to load more alerts.";
                }
            });
        }

        if (alertModalClose) alertModalClose.addEventListener("click", closeAlertModal);

        if (alertModal) {
            alertModal.addEventListener("click", (event) => {
                if (event.target?.dataset?.close === "alert-modal") closeAlertModal();
            });
        }

        document.addEventListener("keydown", (event) => {
            if (event.key !== "Escape") return;
            if (alertModal && !alertModal.classList.contains("alert-modal--hidden")) {
                closeAlertModal();
                return;
            }
            if (alertDrawer && !alertDrawer.classList.contains("alert-drawer--hidden")) {
                closeAlertDrawer();
            }
        });
    }

    return {
        openAlertDrawer,
        closeAlertDrawer,
        closeAlertModal,
        bindEvents,
    };
}
