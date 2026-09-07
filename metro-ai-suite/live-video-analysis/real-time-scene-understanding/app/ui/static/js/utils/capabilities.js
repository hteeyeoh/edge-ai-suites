/* Copyright (C) 2026 Intel Corporation */
/* SPDX-License-Identifier: Apache-2.0 */

/**
 * Capability-domain utility helpers.
 * Interprets capability payloads and builds metric-chip tooltip details.
 */

function numberOrNull(value) {
    return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function formatGiBFromBytes(value) {
    const bytes = numberOrNull(value);
    if (bytes === null || bytes <= 0) return null;

    const gib = bytes / (1024 ** 3);
    return `${gib >= 10 ? gib.toFixed(0) : gib.toFixed(1)} GiB`;
}

function formatInstalledMemory(platform) {
    if (!platform || !platform.system_memory) return null;

    const installedGiB = numberOrNull(platform.system_memory.installed_gib);
    if (installedGiB === null || installedGiB <= 0) return null;

    const value = Number.isInteger(installedGiB) ? installedGiB : installedGiB.toFixed(2);
    return `${value} GiB RAM`;
}

export function hasOpenvinoGpuInference(device) {
    return Array.isArray(device && device.sw_functional_capabilities)
        && device.sw_functional_capabilities.includes("openvino_gpu_inference");
}

export function buildCapabilitiesFallback() {
    return {
        has_gpu: null,
        has_npu: null,
        devices: [],
        platform: null,
    };
}

function deviceName(device, fallback) {
    const name = device && device.commercial_reference;
    return typeof name === "string" && name.trim() ? name.trim() : fallback;
}

function buildDetailLinesFromDevice(device) {
    const lines = [];
    if (!device) return lines;

    if (device.vendor) lines.push(`Vendor: ${device.vendor}`);
    if (device.category) lines.push(`Category: ${String(device.category).toUpperCase()}`);

    const driver = device.details && device.details.driver_name;
    if (driver) lines.push(`Driver: ${driver}`);

    const memory = device.details && device.details.memory ? device.details.memory.total_bytes : null;
    const memoryLabel = formatGiBFromBytes(memory);
    if (memoryLabel) lines.push(`Memory: ${memoryLabel}`);

    return lines;
}

export function enrichCapabilities(data) {
    const devices = Array.isArray(data && data.devices) ? data.devices : null;
    if (!devices) throw new Error("Invalid capability response");

    return {
        ...data,
        has_gpu: devices.some((device) => {
            const category = device && device.category;
            return device && device.present === true
                && (category === "igpu" || category === "dgpu")
                && hasOpenvinoGpuInference(device);
        }),
        has_npu: devices.some((device) => device && device.present === true && device.category === "npu"),
    };
}

export function buildMetricChipDetailMap(capabilities) {
    const rawDevices = Array.isArray(capabilities && capabilities.devices) ? capabilities.devices : [];
    const devices = rawDevices.filter((device) => device && device.present === true);
    const cpu = devices.find((device) => device.category === "cpu");
    const gpus = devices.filter((device) => {
        const category = device.category;
        return (category === "igpu" || category === "dgpu") && hasOpenvinoGpuInference(device);
    });
    const npu = devices.find((device) => device.category === "npu");

    const map = {
        cpu: cpu ? buildDetailLinesFromDevice(cpu) : [],
        ram: [],
        gpu: [],
        npu: npu ? buildDetailLinesFromDevice(npu) : [],
    };

    const installedMemory = formatInstalledMemory(capabilities && capabilities.platform ? capabilities.platform : null);
    if (installedMemory) {
        map.ram.push(`Installed memory: ${installedMemory}`);
    }

    if (Array.isArray(gpus) && gpus.length > 0) {
        const names = gpus.map((device) => deviceName(device, "GPU"));
        map.gpu.push(`Detected GPUs: ${names.join(", ")}`);
        gpus.forEach((device) => {
            buildDetailLinesFromDevice(device).forEach((line) => {
                if (!map.gpu.includes(line)) {
                    map.gpu.push(line);
                }
            });
        });
    }

    return map;
}
