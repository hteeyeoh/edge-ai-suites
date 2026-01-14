/**
 * API service for backend communication
 */
const ApiService = (function () {
    const DEFAULT_MODEL = 'InternVL2-1B';
    const DEFAULT_DETECTION_MODEL = 'yolov8s';
    const DEFAULT_PIPELINE = 'GenAI_Pipeline_on_CPU';

    async function fetchModels() {
        try {
            const resp = await fetch('/api/models');
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            return data?.models || [DEFAULT_MODEL];
        } catch (_err) {
            return [DEFAULT_MODEL];
        }
    }

    async function fetchDetectionModels() {
        try {
            const resp = await fetch('/api/detection-models');
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            return data?.models || ['yolov8s'];
        } catch (_err) {
            return [DEFAULT_DETECTION_MODEL];
        }
    }

    async function fetchPipelines() {
        try {
            const resp = await fetch('/api/pipelines');
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            return data?.pipelines || [DEFAULT_PIPELINE];
        } catch (_err) {
            return [DEFAULT_PIPELINE];
        }
    }

    async function fetchRuns() {
        const resp = await fetch('/api/runs');
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        return await resp.json();
    }

    async function startRun(requestBody) {
        const resp = await fetch('/api/runs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestBody)
        });
        const data = await resp.json().catch(async () => ({ message: await resp.text() }));
        if (!resp.ok) throw new Error(data?.message || data?.detail?.message || resp.statusText);
        return data;
    }

    async function stopRun(runId) {
        const resp = await fetch(`/api/runs/${runId}`, { method: 'DELETE' });
        if (!resp.ok) {
            if (resp.status === 404 || resp.status === 502) {
                return { notFound: true };
            }
            const data = await resp.json().catch(async () => ({ message: await resp.text() }));
            throw new Error(data?.message || data?.detail?.message || resp.statusText);
        }
        return await resp.json();
    }

    return {
        fetchModels,
        fetchDetectionModels,
        fetchPipelines,
        fetchRuns,
        startRun,
        stopRun,
        DEFAULT_MODEL,
        DEFAULT_PIPELINE
    };
})();
