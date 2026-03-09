/**
 * Main application entry point
 */

(function () {

    const $ = (sel) => document.querySelector(sel);
    const els = {
        themeToggle: $('#themeToggle'),
        composer: $('#composer'),
        input: $('#userInput'),
        sendBtn: $('#sendBtn'),
        messages: $('#messages'),
        hint: $('#chatHint'),
        newChatBtn: $('#newChatBtn'),
    };

    ThemeManager.applyTheme(ThemeManager.detectInitialTheme(), els.themeToggle);
    if (els.themeToggle) {
        els.themeToggle.addEventListener('click', () => {
            ThemeManager.toggleTheme(els.themeToggle);
            ChartManager.updateChartColors();
        });
    }

    // -----------------------------
    // CHAT UI + CONVERSATION HISTORY (streaming POST /api/chat with SSE)
    // -----------------------------

    // If 'els' already exists in your IIFE, just ensure these are present:
    els.convList = document.querySelector('.conv-list');
    els.messages = els.messages || document.getElementById('messages');
    els.hint = els.hint || document.getElementById('chatHint');
    els.composer = els.composer || document.getElementById('composer');
    els.input = els.input || document.getElementById('userInput');
    els.sendBtn = els.sendBtn || document.getElementById('sendBtn');

    // --- Composer lock / unlock (single-turn mode) ---
    function lockComposer() {
        if (!els.composer) return;

        // add class that hides the whole composer row
        els.composer.classList.add('is-locked');

        // defensive: disable inner controls too (in case CSS is changed later)
        if (els.input) {
            els.input.disabled = true;
            els.input.setAttribute('aria-disabled', 'true');
        }
        if (els.sendBtn) {
            els.sendBtn.disabled = true;
            els.sendBtn.setAttribute('aria-disabled', 'true');
        }
    }

    function unlockComposer() {
        if (!els.composer) return;

        // show composer row again
        els.composer.classList.remove('is-locked');

        if (els.input) {
            els.input.disabled = false;
            els.input.removeAttribute('aria-disabled');
            els.input.value = '';
            els.input.style.height = 'auto';
        }
        if (els.sendBtn) {
            els.sendBtn.disabled = false;
            els.sendBtn.removeAttribute('aria-disabled');
        }
    }

    // ---------- State & persistence ----------
    const STORAGE_KEY = 'lvc-conversations-v1';

    // shape:
    // state = {
    //   conversations: [ { id, title, createdAt, messages: [{role:'user'|'bot', text, ts}], framesMeta? } ],
    //   activeId: '...'
    // }
    let state = loadState();

    function loadState() {
        try {
            const raw = localStorage.getItem(STORAGE_KEY);
            const parsed = raw ? JSON.parse(raw) : null;
            if (parsed && Array.isArray(parsed.conversations)) return parsed;
        } catch { }
        return { conversations: [], activeId: null };
    }

    function persist() {
        try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch { }
    }

    function uid() {
        return (crypto?.randomUUID?.() || `id_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`);
    }

    function titleFrom(text) {
        return (text || '').replace(/\s+/g, ' ').trim().slice(0, 60) || 'New chat';
    }

    function getActiveConversation() {
        return state.conversations.find(c => c.id === state.activeId) || null;
    }

    function bumpActiveToTop() {
        const idx = state.conversations.findIndex(c => c.id === state.activeId);
        if (idx > 0) {
            const [c] = state.conversations.splice(idx, 1);
            state.conversations.unshift(c);
        }
    }

    function ensureActiveConversation(initialTitle) {
        if (state.activeId && getActiveConversation()) return state.activeId;
        const id = uid();
        const conv = {
            id,
            title: titleFrom(initialTitle),
            createdAt: Date.now(),
            messages: []
        };
        state.conversations.unshift(conv);
        state.activeId = id;
        persist();
        renderConvList();
        return id;
    }

    function addMessageToActive(role, text) {
        const conv = getActiveConversation();
        if (!conv) return;
        conv.messages.push({ role, text, ts: Date.now() });
        persist();
    }

    function setFramesMetaForActive(meta) {
        const conv = getActiveConversation();
        if (!conv) return;
        conv.framesMeta = meta;
        persist();
    }

    // ---- Model info (fetched once and cached)
    let llmModelName = null;
    let llmModelLoaded = false;

    async function loadModelInfoOnce() {
        if (llmModelLoaded) return llmModelName;
        try {
            const res = await fetch('/api/model', { headers: { 'Accept': 'application/json' } });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            llmModelName = data?.llm_model || 'Unknown model';
        } catch (e) {
            console.warn('Failed to load /api/model:', e);
            llmModelName = 'Unknown model';
        } finally {
            llmModelLoaded = true;
        }
        return llmModelName;
    }

    loadModelInfoOnce();

    // ---------- Rendering ----------
    function renderConvList() {
        if (!els.convList) return;
        els.convList.innerHTML = '';

        for (const c of state.conversations) {
            const li = document.createElement('li');
            li.className = 'conv-item' + (c.id === state.activeId ? ' active' : '');
            li.dataset.id = c.id;

            // Title
            const title = document.createElement('span');
            title.className = 'conv-title';
            title.textContent = c.title;

            // Actions (delete)
            const actions = document.createElement('div');
            actions.className = 'conv-actions';

            const delBtn = document.createElement('button');
            delBtn.className = 'icon-btn-ghost delete-chat';
            delBtn.type = 'button';
            delBtn.title = 'Delete conversation';
            delBtn.setAttribute('aria-label', 'Delete conversation');

            // Trash icon (themed via currentColor)
            delBtn.innerHTML = `
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="M9 3h6l1 2h4v2H4V5h4l1-2Zm1 6h2v8h-2V9Zm4 0h2v8h-2V9ZM7 9h2v8H7V9Z"
              fill="currentColor"/>
      </svg>
    `;

            actions.appendChild(delBtn);

            // Compose row
            li.appendChild(title);
            li.appendChild(actions);
            els.convList.appendChild(li);
        }

        // Keep the "New chat" button visible only when there is at least 1 conversation
        updateNewChatButtonVisibility?.();
    }

    function renderConversation(id) {
        const conv = state.conversations.find(c => c.id === id);
        if (!conv || !els.messages) return;

        els.messages.innerHTML = '';
        if (els.hint) els.hint.style.display = conv.messages.length ? 'none' : '';

        for (const m of conv.messages) {
            if (m.role === 'user') {
                const div = document.createElement('div');
                div.className = 'msg user';
                div.textContent = m.text;
                els.messages.appendChild(div);
            } else {
                const div = document.createElement('div');
                div.className = 'msg bot';

                const textEl = document.createElement('div');
                textEl.className = 'msg-text';
                textEl.textContent = m.text || '';

                const metaEl = document.createElement('div');
                metaEl.className = 'msg-meta';
                // Fill with model name (cached)
                // We won't await here to avoid blocking render; set after tick.
                (async () => {
                    const modelName = await loadModelInfoOnce();
                    metaEl.textContent = `Response generated by ${modelName}.`;
                })();

                div.appendChild(textEl);
                div.appendChild(metaEl);
                els.messages.appendChild(div);
            }
        }

        // ⤵️ If you keep frames at conversation-level, attach to the last bot bubble
        if (conv.framesMeta && conv.framesMeta.length) {
            const botBubbles = els.messages.querySelectorAll('.msg.bot');
            const lastBot = botBubbles.length ? botBubbles[botBubbles.length - 1] : null;
            if (lastBot) renderFramesInsideBubble(lastBot, conv.framesMeta); // not awaited
        }

        els.messages.scrollTop = els.messages.scrollHeight;
    }

    function setActive(id) {
        const exists = state.conversations.some(c => c.id === id);
        if (!exists) return;
        state.activeId = id;
        persist();
        renderConvList();
        renderConversation(id);

        // ⤵️ Single-turn rule:
        const conv = state.conversations.find(c => c.id === id);
        if (conv && conv.messages && conv.messages.length > 0) {
            lockComposer();   // existing convo → view-only
        } else {
            unlockComposer(); // empty convo (rare, unless you precreate) → allow
        }
    }

    // Initial paint
    renderConvList();
    if (state.activeId) {
        renderConversation(state.activeId);
        updateNewChatButtonVisibility();
        const conv = state.conversations.find(c => c.id === state.activeId);
        if (conv && conv.messages.length > 0) lockComposer(); else unlockComposer();
    } else {
        // no active conversation yet → allow first question
        unlockComposer();
    }

    // if (state.activeId) renderConversation(state.activeId);

    // ---------- Chat UI helpers ----------
    const autoGrow = () => {
        if (!els.input) return;
        els.input.style.height = 'auto';
        els.input.style.height = Math.min(els.input.scrollHeight, 200) + 'px';
    };
    els.input?.addEventListener('input', autoGrow);
    window.addEventListener('resize', autoGrow);
    autoGrow();

    // Submit on Enter (Shift+Enter => newline)
    els.input?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            els.composer?.requestSubmit();
        }
    });

    const setBusy = (busy) => {
        if (els.sendBtn) els.sendBtn.disabled = busy;
        if (els.input) els.input.disabled = busy;
    };

    // Make a chat bubble in DOM
    const appendMessage = (text, who /* 'user' | 'bot' */) => {
        const div = document.createElement('div');
        div.className = `msg ${who}`;

        if (who === 'bot') {
            // Structured bot bubble: text + meta
            const textEl = document.createElement('div');
            textEl.className = 'msg-text';
            textEl.textContent = text || '';

            const metaEl = document.createElement('div');
            metaEl.className = 'msg-meta';
            // Fill later once we know model (and after stream end if you prefer)
            // Put a placeholder to prevent shifting
            metaEl.textContent = ' ';

            div.appendChild(textEl);
            div.appendChild(metaEl);
        } else {
            // user bubble stays simple for now
            div.textContent = text || '';
        }

        els.messages?.appendChild(div);
        if (els.messages) els.messages.scrollTop = els.messages.scrollHeight;

        return div;
    };


    // Spacing heuristic (prevents "wordglue")
    const PUNCTUATION = new Set([',', '.', '!', '?', ':', ';', ')', ']', '}', '”', '’']);
    function shouldInsertSpace(prev, next) {
        if (!prev || !next) return false;
        const last = prev[prev.length - 1];
        const first = next[0];
        if (/\s/.test(last)) return false;
        if (/\s/.test(first)) return false;
        if (PUNCTUATION.has(first)) return false;
        return true;
    }

    // Strict SSE reader (handles event: & data:)
    function createStrictSSEReader(onEvent) {
        let buffer = '';
        return {
            feed(chunk) {
                buffer += chunk;
                const frames = buffer.split(/\r?\n\r?\n/);
                buffer = frames.pop();

                for (const frame of frames) {
                    let eventName = 'message';
                    const dataLines = [];
                    for (const line of frame.split(/\r?\n/)) {
                        if (line.startsWith('event:')) {
                            eventName = line.slice(6).trim() || 'message';
                        } else if (line.startsWith('data:')) {
                            dataLines.push(line.slice(5)); // keep leading spaces
                        }
                    }
                    onEvent({ event: eventName, data: dataLines.join('\n') });
                }
            },
            flush() {
                if (buffer.trim()) onEvent({ event: 'message', data: buffer });
                buffer = '';
            }
        };
    }

    // Extract text from payload (raw or JSON)
    function extractText(payload) {
        if (!payload) return '';
        const trimmed = payload.trimStart();
        if (trimmed.startsWith('{')) {
            try {
                const obj = JSON.parse(trimmed);
                if (typeof obj === 'string') return obj;
                if (typeof obj?.content === 'string') return obj.content;
                if (typeof obj?.delta === 'string') return obj.delta;
                if (typeof obj?.token === 'string') return obj.token;
                if (Array.isArray(obj?.tokens)) return obj.tokens.join('');
                if (Array.isArray(obj?.deltas)) return obj.deltas.join('');
            } catch {
                // not JSON
            }
        }
        return payload;
    }

    // Abort controller for in-flight stream
    let currentController = null;

    // ---- Frames: utils & rendering inside a bot bubble ----
    function isDataURL(str) {
        return typeof str === 'string' && str.startsWith('data:');
    }
    function sniffImageMimeFromBase64(b64) {
        if (!b64) return null;
        if (b64.startsWith('/9j/')) return 'image/jpeg';
        if (b64.startsWith('iVBORw0KGgo')) return 'image/png';
        if (b64.startsWith('UklG')) return 'image/webp';
        return null;
    }
    async function bgraBase64ToDataURL(frameB64, w, h) {
        const binary = atob(frameB64);
        const len = binary.length;
        const buf = new Uint8ClampedArray(len);
        for (let i = 0; i < len; i++) buf[i] = binary.charCodeAt(i);
        // BGRA → RGBA
        for (let i = 0; i < len; i += 4) {
            const b = buf[i], g = buf[i + 1], r = buf[i + 2], a = buf[i + 3];
            buf[i] = r; buf[i + 1] = g; buf[i + 2] = b; buf[i + 3] = a;
        }
        const canvas = document.createElement('canvas');
        canvas.width = w; canvas.height = h;
        const ctx = canvas.getContext('2d');
        ctx.putImageData(new ImageData(buf, w, h), 0, 0);
        return canvas.toDataURL('image/png');
    }

    /**
     * Build a compact frame preview for inside a bubble
     */
    async function buildInlineFrame(frame) {
        const { metadata = {}, preview = '' } = frame || {};
        const { frame_data: raw = '', frame_format: fmt, frame_width: w, frame_height: h } = metadata;

        const wrap = document.createElement('div');
        wrap.className = 'inline-frame';

        const img = document.createElement('img');
        img.className = 'inline-frame-img';
        img.alt = preview || 'Frame';

        try {
            if (isDataURL(raw)) {
                img.src = raw;
            } else {
                const sniffed = sniffImageMimeFromBase64(raw);
                if (sniffed) {
                    img.src = `data:${sniffed};base64,${raw}`;
                } else if ((fmt || '').toUpperCase() === 'BGRA' && Number.isFinite(w) && Number.isFinite(h)) {
                    img.src = await bgraBase64ToDataURL(raw, w, h);
                } else {
                    img.src = `data:image/jpeg;base64,${raw}`;
                }
            }
        } catch (e) {
            console.error('inline frame build failed', e);
        }

        const cap = document.createElement('div');
        cap.className = 'inline-frame-cap';
        cap.textContent = preview || '';

        wrap.appendChild(img);
        wrap.appendChild(cap);
        return wrap;
    }

    /**
     * Render one or many frames **inside** a bot bubble, under its text
     */
    async function renderFramesInsideBubble(botBubbleEl, frames) {
        if (!botBubbleEl || !frames) return;
        // accept string payload (stringified JSON) or array
        if (typeof frames === 'string') {
            try { frames = JSON.parse(frames); } catch { return; }
        }
        if (!Array.isArray(frames) || frames.length === 0) return;

        // Container inside bubble
        let gallery = botBubbleEl.querySelector('.inline-frame-gallery');
        if (!gallery) {
            gallery = document.createElement('div');
            gallery.className = 'inline-frame-gallery';
            botBubbleEl.appendChild(gallery);
        }

        for (const f of frames) {
            const node = await buildInlineFrame(f);
            gallery.appendChild(node);
        }

        // ⤵️ Always keep meta as the last child in the bubble
        const meta = botBubbleEl.querySelector('.msg-meta');
        if (meta) botBubbleEl.appendChild(meta);
    }


    // Stream chat and return the full bot text + frames meta
    async function streamChat(userText) {
        if (currentController) currentController.abort();
        currentController = new AbortController();

        const botDiv = appendMessage('', 'bot');
        const botTextEl = botDiv.querySelector('.msg-text');   // <— NEW
        const botMetaEl = botDiv.querySelector('.msg-meta');   // <— NEW

        let fullBotText = '';
        let framesMeta = null;

        try {
            const res = await fetch('/api/chat', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'text/event-stream'
                },
                body: JSON.stringify({ input: userText }),
                signal: currentController.signal
            });

            if (!res.ok || !res.body) {
                throw new Error(`HTTP ${res.status} ${res.statusText}`);
            }

            const reader = res.body.getReader();
            const decoder = new TextDecoder('utf-8');

            const sse = createStrictSSEReader(({ event, data }) => {
                if (data === '[DONE]') return;

                if (event === 'frame') {
                    try { framesMeta = JSON.parse(data); } catch { }
                    return; // do not append into text
                }

                const text = extractText(data);
                if (!text) return;

                // stream into .msg-text instead of the bubble itself
                const prev = botTextEl.textContent || '';
                let chunk = text;

                // If both sides have whitespace, collapse to one space
                if (/\s$/.test(prev) && /^\s/.test(chunk)) {
                    chunk = chunk.replace(/^\s+/, ' ');
                }

                // Append
                botTextEl.textContent += chunk;
                fullBotText += chunk;

                // if (shouldInsertSpace(prev, text)) {
                //     botTextEl.textContent += ' ';
                //     fullBotText += ' ';
                // }
                // botTextEl.textContent += text;
                // fullBotText += text;

                els.messages.scrollTop = els.messages.scrollHeight;
            });

            // Read stream
            while (true) {
                const { value, done } = await reader.read();
                if (done) break;
                sse.feed(decoder.decode(value, { stream: true }));
            }
            sse.flush();

            // As soon as we finish (or earlier if you prefer), ensure model is shown
            const modelName = await loadModelInfoOnce();
            if (botMetaEl) {
                botMetaEl.textContent = `Response generated by ${modelName}.`;
            }

            return { botText: fullBotText, framesMeta, botBubble: botDiv };
        } finally {
            currentController = null;
        }
    }

    // Submit handler — wires everything together with history
    els.composer?.addEventListener('submit', async (e) => {
        e.preventDefault();

        // Don't submit if single-turn lock is active
        if (els.composer?.classList.contains('is-locked')) return;

        const text = (els.input?.value || '').trim();
        if (!text) return;

        // Ensure active conversation (first message sets the title)
        ensureActiveConversation(text);

        // Hide hint once first message lands
        if (els.hint) els.hint.style.display = 'none';

        // UI + state: user message
        appendMessage(text, 'user');
        addMessageToActive('user', text);

        // Clear input
        els.input.value = '';
        autoGrow();

        try {
            setBusy(true);
            const { botText, framesMeta, botBubble } = await streamChat(text);

            // State: bot message + frames metadata
            if (botText) addMessageToActive('bot', botText);
            if (framesMeta) setFramesMetaForActive(framesMeta);

            // ⤵️ place frames **inside** the same bubble
            if (framesMeta && botBubble) {
                await renderFramesInsideBubble(botBubble.querySelector('.msg-text')?.parentElement || botBubble, framesMeta);
            }

            // Move active to top and refresh list (keeps newest on top)
            bumpActiveToTop();
            renderConvList();

            // ⤵️ Single-turn: lock the composer now
            lockComposer();

        } catch (err) {
            console.error(err);
            appendMessage('Sorry, something went wrong.', 'bot');
            addMessageToActive('bot', 'Sorry, something went wrong.');
        } finally {
            setBusy(false);
        }
    });


    // Start a brand new chat (no record created until the next user message)
    function startNewChat() {
        // Clear current active selection so the next message creates a new conversation
        state.activeId = null;
        persist();

        // Clear UI
        if (els.messages) els.messages.innerHTML = '';
        if (els.hint) els.hint.style.display = '';
        if (els.input) {
            els.input.value = '';
            els.input.style.height = 'auto';
        }

        // Unlock composer in case it was locked
        unlockComposer();

        // Rerender the list (the button visibility will remain based on conversation count)
        renderConvList();
        updateNewChatButtonVisibility();
    }

    // Toggle New Chat button visibility depending on whether there is at least 1 conversation
    // Keep "New" visible once it has been shown at least once
    function updateNewChatButtonVisibility() {
        if (!els.newChatBtn) return;

        // if we've ever shown it before, keep showing it
        const everShown = els.newChatBtn.dataset.everShown === '1';

        if (state.conversations.length > 0) {
            els.newChatBtn.hidden = false;
            els.newChatBtn.dataset.everShown = '1'; // mark as shown forever
        } else {
            // If never shown before (fresh first-load), keep hidden.
            // If shown before, keep visible even when empty.
            els.newChatBtn.hidden = everShown ? false : true;
        }
    }


    // Hook button click
    els.newChatBtn?.addEventListener('click', startNewChat);

    function deleteConversationNoConfirm(id) {
        const idx = state.conversations.findIndex(c => c.id === id);
        if (idx === -1) return;

        const deletingActive = (state.activeId === id);

        // Remove from array
        state.conversations.splice(idx, 1);

        // Choose next active
        if (deletingActive) {
            state.activeId = state.conversations[0]?.id ?? null;
        }

        persist();
        renderConvList();
        updateNewChatButtonVisibility();

        if (state.activeId) {
            renderConversation(state.activeId);
        } else {
            if (els.messages) els.messages.innerHTML = '';
            if (els.hint) els.hint.style.display = '';
            // (optional) unlock composer if you want "New" + empty canvas
            unlockComposer?.();
        }

    }

    function showInlineConfirm(li) {
        // Remove any existing confirm chips
        document.querySelectorAll('.confirm-chip').forEach(c => c.remove());

        const chip = document.createElement('span');
        chip.className = 'confirm-chip';
        chip.innerHTML = `
    <span>Delete?</span>
    <button type="button" class="btn-confirm-yes danger">Delete</button>
    <button type="button" class="btn-confirm-no">Cancel</button>
  `;
        li.appendChild(chip);

        // YES → delete
        chip.querySelector('.btn-confirm-yes').addEventListener('click', (ev) => {
            ev.stopPropagation();
            deleteConversationNoConfirm(li.dataset.id);
            chip.remove();
        });

        // NO → dismiss
        chip.querySelector('.btn-confirm-no').addEventListener('click', (ev) => {
            ev.stopPropagation();
            chip.remove();
        });
    }

    els.convList?.addEventListener('click', (e) => {
        const delBtn = e.target.closest('.delete-chat');
        const li = e.target.closest('.conv-item');

        // Case 1: delete icon clicked
        if (delBtn && li) {
            e.stopPropagation(); // DO NOT select the conversation
            showInlineConfirm(li);
            return;
        }

        // Case 2: clicked somewhere else inside list → select conversation
        if (li) {
            setActive(li.dataset.id);
        }
    });


})();