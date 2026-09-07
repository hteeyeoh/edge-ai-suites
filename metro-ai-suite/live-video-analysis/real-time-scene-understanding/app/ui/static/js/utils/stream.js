/* Copyright (C) 2026 Intel Corporation */
/* SPDX-License-Identifier: Apache-2.0 */

/**
 * Stream-domain formatting and normalization helpers.
 * Keeps stream controller logic focused on orchestration instead of text rules.
 */

function formatMetricNumber(value, digits = 1) {
    if (value === null || value === undefined || value === "") return "-";
    const num = Number(value);
    if (!Number.isFinite(num)) return "-";
    return num.toFixed(digits);
}

export function sanitizeStreamId(value) {
    return value
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9_-]+/g, "-")
        .replace(/^-+|-+$/g, "")
        .slice(0, 48);
}

export function formatAlertEventDetails(stream) {
    const eventName = (stream.alert_event || "").trim();
    return eventName
        ? `Alert Event: ${eventName}`
        : "Alert Event: not provided";
}

export function isAlertDetected(captionText) {
    const normalized = String(captionText || "").trim().toLowerCase();
    if (!normalized) return false;

    // VLM is configured for binary responses (Yes/No), so treat Yes as alert.
    return /^yes\b/.test(normalized);
}

export function formatVlmMetrics(stream) {
    const ttft = formatMetricNumber(stream.ttft_ms);
    const tpot = formatMetricNumber(stream.tpot_ms);
    const throughput = formatMetricNumber(stream.throughput_tps);
    return `TTFT: ${ttft} ms | TPOT: ${tpot} ms | Throughput: ${throughput} tok/s`;
}
