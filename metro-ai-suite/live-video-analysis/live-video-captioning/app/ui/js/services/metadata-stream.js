/**
 * Metadata stream service for SSE handling
 */
const MetadataStreamService = (function () {
    let metadataSource = null;
    const runUIs = new Map();
    const lastCaptionTime = new Map();

    function initMultiplexedMetadataStream(cfg) {
        // Single SSE connection for all run metadata to avoid browser connection limits
        if (metadataSource) {
            return; // Already initialized
        }

        console.log('Initializing multiplexed metadata stream...');
        metadataSource = new EventSource('/api/runs/metadata-stream');

        metadataSource.onopen = () => {
            console.log('Multiplexed metadata stream connected');
        };

        metadataSource.onmessage = (event) => {
            if (!event.data) return;

            try {
                const msg = JSON.parse(event.data);
                const runId = msg.runId;

                if (!runId) {
                    console.warn('Received metadata without runId:', msg);
                    return;
                }

                // Handle run removal notification
                if (msg.removed) {
                    console.log(`Run ${runId} removed from server`);
                    return;
                }

                // Get the UI elements for this run
                const ui = runUIs.get(runId);
                if (!ui) {
                    console.log(`No UI found for run ${runId}, ignoring metadata`);
                    return; // No UI for this run yet
                }

                // msg.data is already parsed; extract caption and metrics directly
                const data = msg.data;
                const captionText = typeof data === 'object' && data.result ? data.result : (typeof data === 'string' ? data : JSON.stringify(data));
                ui.caption.textContent = captionText;

                // Agent Mode: Check for "Yes" or "No" in caption and apply alert styling
                if (cfg && cfg.agentMode) {
                    const runCard = ui.wrap;
                    const captionPanel = ui.captionPanel;
                    const lowerCaption = captionText ? captionText.toLowerCase() : '';

                    if (lowerCaption.includes('yes')) {
                        // Red alert for "yes"
                        if (runCard) {
                            runCard.classList.add('alert-active');
                            runCard.classList.remove('safe-active');
                        }
                        if (captionPanel) {
                            captionPanel.classList.add('alert-active');
                            captionPanel.classList.remove('safe-active');
                        }
                        // Show a popup tied to THIS card (follows it on scroll/resize)
                        showAnchoredAlertPopup(
                            'ALERT: Anomaly detected in video feed. Please investigate immediately.',
                            runCard || captionPanel,
                            {
                                type: 'danger',
                                duration: 1000,
                                // Use a stable id so repeated 'yes' on the same card updates the same popup
                                id: runCard && runCard.dataset.runId,
                                // Tweak position if needed:
                                offsetX: 8,   // pixels inward from card’s right edge
                                offsetY: 8    // pixels down from card’s top edge
                            }
                        );

                    } else if (lowerCaption.includes('no')) {
                        // Green indicator for "no"
                        if (runCard) {
                            runCard.classList.add('safe-active');
                            runCard.classList.remove('alert-active');
                        }
                        if (captionPanel) {
                            captionPanel.classList.add('safe-active');
                            captionPanel.classList.remove('alert-active');
                        }
                    } else {
                        // No keyword detected - remove both states
                        if (runCard) {
                            runCard.classList.remove('alert-active');
                            runCard.classList.remove('safe-active');
                        }
                        if (captionPanel) {
                            captionPanel.classList.remove('alert-active');
                            captionPanel.classList.remove('safe-active');
                        }
                    }
                }

                // Extract metrics from the data object
                const metrics = (typeof data === 'object' && data.metrics) ? data.metrics : {};
                const throughput = metrics.throughput_mean;
                const timestampText =
                    data.timestamp_seconds !== undefined
                        ? `Updated ${data.timestamp_seconds.toFixed(2)}s into stream`
                        : data.timestamp
                            ? `Updated at ${new Date(data.timestamp).toLocaleTimeString()}`
                            : '—';
                ui.chips.querySelector('[data-ttft]').textContent = metrics.ttft_mean ? `${metrics.ttft_mean.toFixed(0)} ms` : '—';
                ui.chips.querySelector('[data-tpot]').textContent = metrics.tpot_mean ? `${metrics.tpot_mean.toFixed(2)} ms` : '—';
                ui.chips.querySelector('[data-throughput]').textContent = throughput ? `${throughput.toFixed(2)} tok/s` : '—';

                // Calculate lag: time since this caption was received by the browser
                // We use the browser's own timestamp to avoid clock sync issues with the container
                const receivedAtMs = Date.now();
                // Store when this run last received a caption update
                lastCaptionTime.set(runId, receivedAtMs);
                ui.chips.querySelector('[data-lag]').textContent = '0.00s';

                ui.timestamp.textContent = timestampText;

                console.log(`Updated metadata for run ${runId}`);
            } catch (err) {
                console.error('Error parsing metadata:', err, 'Event data:', event.data);
            }
        };

        metadataSource.onerror = (event) => {
            console.error('Metadata stream error:', event);
            // EventSource will automatically try to reconnect
            // Reset the connection after a delay if it keeps failing
            setTimeout(() => {
                if (metadataSource && metadataSource.readyState === EventSource.CLOSED) {
                    console.log('Reconnecting metadata stream...');
                    metadataSource = null;
                    initMultiplexedMetadataStream(cfg);
                }
            }, 5000);
        };

        metadataSource.onclose = () => {
            console.log('Metadata stream closed');
            metadataSource = null;
        };
    }

    /**
     * Returns scrollable ancestors so the popup can reposition on scroll.
     */
    function getScrollParents(el) {
        const parents = [];
        let node = el ? el.parentElement : null;
        const overflowRegex = /(auto|scroll|overlay)/;

        while (node && node !== document.body) {
            const s = getComputedStyle(node);
            const isScrollable =
                overflowRegex.test(s.overflow) ||
                overflowRegex.test(s.overflowY) ||
                overflowRegex.test(s.overflowX);
            if (isScrollable) parents.push(node);
            node = node.parentElement;
        }
        parents.push(window);
        return parents;
    }

    /**
     * Show (or update) a popup visually anchored to `anchorEl` but rendered in <body>.
     * Simple variant: icon + message + close, auto-dismiss.
     *
     * @param {string} message  - Text to display.
     * @param {HTMLElement} anchorEl - The card (or header) to anchor near.
     * @param {object} [opts]
     * @param {'danger'|'safe'|'warn'} [opts.type='danger'] - Visual style.
     * @param {number} [opts.duration=5000] - Auto-dismiss ms.
     * @param {string} [opts.id] - Stable id for this card (e.g., runId) to update instead of stacking.
     * @param {number} [opts.offsetX=12] - Inset from card's right edge.
     * @param {number} [opts.offsetY=12] - Inset from card's top edge.
     */
    function showAnchoredAlertPopup(message, anchorEl, opts = {}) {
        const {
            type = 'danger',
            duration = 5000,
            id,
            offsetX = 12,
            offsetY = 12
        } = opts;

        if (!anchorEl || !(anchorEl instanceof HTMLElement)) {
            console.warn('showAnchoredAlertPopup: invalid anchor element');
            return null;
        }

        // Reuse existing popup for the same id (if provided)
        let popup = id ? document.querySelector(`.alert-popup[data-id="${id}"]`) : null;
        const isNew = !popup;

        if (!popup) {
            popup = document.createElement('div');
            popup.className = 'alert-popup'; // base class; CSS handles styling
            if (id) popup.dataset.id = id;
            popup.setAttribute('role', 'alert');
            popup.setAttribute('aria-live', 'assertive');

            const icon = document.createElement('span');
            icon.className = 'icon';
            icon.setAttribute('aria-hidden', 'true');
            icon.textContent = type === 'safe' ? '✔' : (type === 'warn' ? '⚠' : '⛔');

            const msg = document.createElement('div');
            msg.className = 'message';
            msg.textContent = message;

            const close = document.createElement('button');
            close.className = 'close-btn';
            close.setAttribute('aria-label', 'Dismiss alert');
            close.textContent = '×';

            popup.appendChild(icon);
            popup.appendChild(msg);
            popup.appendChild(close);

            document.body.appendChild(popup);

            // Manual close
            close.addEventListener('click', () => {
                if (popup._timer) clearTimeout(popup._timer);
                dismiss();
            });
        } else {
            // Update text and icon if we are reusing the same popup
            const msg = popup.querySelector('.message');
            if (msg) msg.textContent = message;
            const icon = popup.querySelector('.icon');
            if (icon) icon.textContent = type === 'safe' ? '✔' : (type === 'warn' ? '⚠' : '⛔');
        }

        // Sync visual variant classes
        popup.classList.toggle('safe', type === 'safe');
        popup.classList.toggle('warn', type === 'warn');
        // (no extra classes needed for 'danger' – base style covers it)

        // Position near the card's bottom-right corner, clamped to viewport
        function positionPopup() {
            const rect = anchorEl.getBoundingClientRect();
            const desiredWidth = Math.min(360, window.innerWidth - 16);

            // Width first
            popup.style.position = 'fixed';
            popup.style.width = `${desiredWidth}px`;
            popup.style.zIndex = 2147483647;

            // NEW: Bottom-right anchor
            let top = rect.bottom - popup.offsetHeight - offsetY;
            let left = rect.right - desiredWidth - offsetX;

            // Clamp inside viewport
            const gutter = 8;
            top = Math.max(gutter, Math.min(top, window.innerHeight - gutter - popup.offsetHeight));
            left = Math.max(gutter, Math.min(left, window.innerWidth - gutter - desiredWidth));

            popup.style.top = `${top}px`;
            popup.style.left = `${left}px`;
        }


        // Dismiss with a small exit animation (matches CSS @keyframes popup-out)
        const dismiss = () => {
            if (popup.dataset.dismissing === '1') return;
            popup.dataset.dismissing = '1';
            popup.style.animation = 'popup-out 180ms ease-in forwards';
            setTimeout(() => {
                scrollParents.forEach(p => p.removeEventListener('scroll', positionPopup, true));
                window.removeEventListener('resize', positionPopup);
                intersection?.disconnect();
                popup.remove();
            }, 200);
        };

        // Auto-dismiss (reset when updating same id)
        if (popup._timer) clearTimeout(popup._timer);
        popup._timer = setTimeout(dismiss, duration);

        // Keep aligned during scroll/resize
        const scrollParents = getScrollParents(anchorEl);
        scrollParents.forEach(p => p.addEventListener('scroll', positionPopup, true));
        window.addEventListener('resize', positionPopup);

        // Hide when the card is out of view; re-show when back
        let intersection = null;
        if ('IntersectionObserver' in window) {
            intersection = new IntersectionObserver(entries => {
                const e = entries[0];
                popup.style.display = e.isIntersecting ? 'flex' : 'none';
                if (e.isIntersecting) positionPopup();
            }, { root: null, threshold: 0 });
            intersection.observe(anchorEl);
        }

        // Initial placement
        positionPopup();

        return popup;
    }

    function registerRunUI(runId, ui) {
        runUIs.set(runId, ui);
    }

    function unregisterRunUI(runId) {
        runUIs.delete(runId);
        lastCaptionTime.delete(runId);
    }

    function getLastCaptionTime(runId) {
        return lastCaptionTime.get(runId);
    }

    function getRunUIs() {
        return runUIs;
    }

    function close() {
        if (metadataSource) {
            console.log('Closing metadata stream');
            metadataSource.close();
            metadataSource = null;
        }
    }

    return {
        initMultiplexedMetadataStream,
        registerRunUI,
        unregisterRunUI,
        getLastCaptionTime,
        getRunUIs,
        close
    };
})();
