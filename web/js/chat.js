/* ---------------------------------------------------------------------------
 * chat.js
 *
 * The message thread, markdown rendering, and the streaming turn.
 *
 * Extracted from the single 4000-line script.js by /data/p9_3b.py.
 * [M14 P9.3]
 * ------------------------------------------------------------------------- */

import { EVENTS, emit } from './bus.js';
import { API, AVATAR_ICON_ASSISTANT, AVATAR_ICON_USER, CAM_BYPASS_TOKEN, STREAM_IDLE_TIMEOUT_MS, STREAM_INCOMPLETE_LABEL, STREAM_TOTAL_TIMEOUT_MS } from './config.js';
import { $, el } from './dom.js';
import { markActiveHistoryItem, setActiveSession, syncUrl } from './history.js';
import { svgIcon } from './icons.js';
import { render as renderMarkdown } from './markdown.js';
import { closeNotesPanel, openNotesPanel } from './notes.js';
import { orbContainer } from './orbctl.js';
import { activityList, activityPanel, activityToggle, appendActivity, renderSearchResults, searchResultsToggle, searchResultsWidget, settingsPanel, showToast, startBackgroundActivityPolling, updatePanelOverlay } from './panels.js';
import { closeRemindersPanel, openRemindersPanel } from './reminders.js';
import { state } from './state.js';
import { camVideo, camVisionModeInput, captureFrameAsBase64Safe, isCameraQuery, startCamera, stopCamera } from './vision.js';
import { pttStopRecording, stopStartupBrief } from './voice.js';
export const chatMessages = $('chat-messages');
export const messageInput = $('message-input');
export const sendBtn      = $('send-btn');
export const stopBtn      = $('stop-btn');
export const charCount    = $('char-count');

export function handleActions(actions, contentEl) {
    if (!actions) return;
    if (!contentEl) return;
    let attempted = false;
    let accepted = true;
    const errors = [];
    const safeOpen = url => {
        attempted = true;
        if (!(url && (url.startsWith('http://') || url.startsWith('https://')))) {
            accepted = false; errors.push('invalid_url'); return;
        }
        let opened = false;
        try {
            const w = window.open(url, '_blank', 'noopener');
            opened = !!w;
        } catch (_) {
            opened = false;
        }
        if (!opened) { accepted = false; errors.push('popup_blocked'); }
        // Browsers block window.open() that isn't triggered by a direct user
        // click (our open arrives async over SSE), so ALWAYS add a clickable
        // link in the chat as a reliable fallback the user can tap.
        try {
            const wrap = document.createElement('div');
            wrap.className = 'msg-actions-links';
            const a = document.createElement('a');
            a.href = url;
            a.target = '_blank';
            a.rel = 'noopener';
            a.className = 'msg-action-link';
            a.textContent = (opened ? '\u2197 Opened: ' : '\u2197 Click to open: ') + url;
            wrap.appendChild(a);
            contentEl.appendChild(wrap);
        } catch (_) {}
        if (!opened) showToast('Pop-up blocked \u2014 tap the link in the chat to open.');
    };
    (actions.wopens || []).forEach(safeOpen);
    (actions.plays || []).forEach(safeOpen);
    (actions.googlesearches || []).forEach(safeOpen);
    (actions.youtubesearches || []).forEach(safeOpen);
    if (actions.images && actions.images.length > 0) {
        attempted = true;
        const wrap = document.createElement('div');
        wrap.className = 'msg-actions-images';
        actions.images.forEach(url => {
            // The model supplies these URLs, so javascript:/data: must never reach src.
            if (typeof url !== 'string' || !/^https?:\/\//i.test(url)) {
                accepted = false;
                errors.push('invalid_image_url');
                return;
            }
            const img = document.createElement('img');
            img.referrerPolicy = 'no-referrer';
            img.src = url;
            img.alt = 'Generated image';
            img.className = 'msg-action-image';
            img.loading = 'lazy';
            img.onerror = () => {
                img.style.display = 'none';
                const fallback = document.createElement('div');
                fallback.className = 'msg-action-image-fallback';
                fallback.textContent = 'Image failed to load.';
                wrap.appendChild(fallback);
            };
            wrap.appendChild(img);
        });
        contentEl.appendChild(wrap);
    }
    if (actions.contents && actions.contents.length > 0) {
        attempted = true;
        const wrap = document.createElement('div');
        wrap.className = 'msg-actions-contents';
        actions.contents.forEach(t => {
            const p = document.createElement('div');
            p.className = 'msg-action-content';
            p.textContent = t;
            wrap.appendChild(p);
        });
        contentEl.appendChild(wrap);
    }
    if (actions.cam) {
        attempted = true;
        if (actions.cam.action === 'open') {
            startCamera();
        } else if (actions.cam.action === 'close') {
            stopCamera();
        } else if (actions.cam.action === 'open_and_capture') {
            const resendMsg = actions.cam.resend_message || 'What do you see?';
            (async () => {
                try {
                    await startCamera();
                    await new Promise((resolve) => {
                        if (!camVideo) { resolve(); return; }
                        if (camVideo.readyState >= 2 && camVideo.videoWidth > 0) {
                            setTimeout(resolve, 500);
                            return;
                        }
                        const onReady = () => {
                            camVideo.removeEventListener('loadeddata', onReady);
                            clearTimeout(t);
                            setTimeout(resolve, 600);
                        };
                        const t = setTimeout(() => {
                            camVideo.removeEventListener('loadeddata', onReady);
                            resolve();
                        }, 4000);
                        camVideo.addEventListener('loadeddata', onReady);
                    });
                    const frame = await captureFrameAsBase64Safe();
                    if (frame) {
                        sendMessage(resendMsg, frame);
                    } else {
                        showToast('Could not capture camera frame. Please try again.');
                    }
                } catch (err) {
                    showToast('Camera access denied.');
                }
            })();
        }
    }
    // Handle panel actions (reminders, notes)
    const panelActions = actions.panels || {};
    if (panelActions.reminders) {
        const p = panelActions.reminders;
        /* Opened by the agent, not the user, so this one may close itself.
             [M14 P10.6] */
        if (p.action === 'open' || p.action === 'refresh') openRemindersPanel({ auto: true });
        else if (p.action === 'close') closeRemindersPanel();
    }
    if (panelActions.notes) {
        const p = panelActions.notes;
        if (p.action === 'open' || p.action === 'refresh') openNotesPanel(p.tab || 'notes', { auto: true });
        else if (p.action === 'close') closeNotesPanel();
    }
    const meta = actions._meta || {};
    if (meta.dispatch_id && meta.action_id) {
        const ack = {
            dispatch_id: meta.dispatch_id, execution_id: meta.execution_id || '',
            action_id: meta.action_id, attempted, accepted: attempted && accepted,
            error: errors.join(',')
        };
        fetch(`${API}/api/activity/frontend-ack`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(ack), keepalive: true
        }).catch(() => {});
        appendActivity({ event: 'frontend_ack', attempted: ack.attempted,
            accepted: ack.accepted, error: ack.error });
    }
}

export function hideWelcome() {
    const w = document.getElementById('welcome-screen');
    if (w) w.remove();
}

// ---- Markdown rendering of assistant replies ------------ [M14 P6] ----
// markdown.js owns .msg-stream-text completely and clears any child it did
// not create, so nothing else may ever append there. handleActions() and the
// stream cursor attach to .msg-content, the PARENT, and that is deliberate.
// P9: drop window.JarvisMarkdown and import { render } from './js/markdown.js'.
export let _mdFrame = 0;
export let _mdPending = null;

export function renderMarkdownInto(contentEl, src, streaming, cursorEl) {
    if (!contentEl) return;
    const host = contentEl.querySelector('.msg-stream-text');
    if (!host) return;
    if (typeof renderMarkdown === 'function') {
        renderMarkdown(host, src, streaming);
    } else {
        // The module failed to load. Degrade to the pre-P6 behaviour rather
        // than leaving the reply blank.
        host.textContent = src;
    }
    host.classList.remove('stream-placeholder');
    placeStreamCursor(host, contentEl, cursorEl);
}

// A rendered block is display:block, so an inline cursor after it drops onto
// a line of its own. When the last block is a paragraph the cursor is moved
// inside it, which keeps it at the end of the sentence being typed; after a
// code block, table or list it stays in the bubble, which reads fine.
export function placeStreamCursor(host, contentEl, cursorEl) {
    if (!cursorEl) return;
    const last = host.lastElementChild;
    const target = last && last.tagName === 'P' ? last : contentEl;
    if (cursorEl.parentNode !== target) target.appendChild(cursorEl);
}

// One render per animation frame. Chunks arrive faster than 60Hz and every
// render touches the DOM, so coalescing is not an optimisation, it is what
// keeps the frame budget. [M14 P6]
export function scheduleMarkdownRender(contentEl, src, streaming, cursorEl) {
    _mdPending = { contentEl: contentEl, src: src, streaming: streaming, cursorEl: cursorEl };
    if (_mdFrame) return;
    _mdFrame = requestAnimationFrame(() => {
        _mdFrame = 0;
        const p = _mdPending;
        _mdPending = null;
        if (!p) return;
        renderMarkdownInto(p.contentEl, p.src, p.streaming, p.cursorEl);
    });
}

export function cancelMarkdownRender() {
    if (_mdFrame) { cancelAnimationFrame(_mdFrame); _mdFrame = 0; }
    _mdPending = null;
}

export function addMessage(role, text) {
    hideWelcome();
    const msg = document.createElement('div');
    msg.className = `message ${role}`;
    const avatar = document.createElement('div');
    avatar.className = 'msg-avatar';
    avatar.innerHTML = role === 'assistant' ? AVATAR_ICON_ASSISTANT : AVATAR_ICON_USER;
    const body = document.createElement('div');
    body.className = 'msg-body';
    const label = document.createElement('div');
    label.className = 'msg-label';
    label.textContent = role === 'assistant'
        ? `Jarvis  (${state.currentMode === 'jarvis' ? 'Jarvis' : state.currentMode === 'realtime' ? 'Realtime' : 'General'})`
        : 'You';
    const content = document.createElement('div');
    content.className = 'msg-content';
    content.textContent = text;
    body.appendChild(label);
    body.appendChild(content);
    msg.appendChild(avatar);
    msg.appendChild(body);
    chatMessages.appendChild(msg);
    // The user's own message justifies moving the view; an assistant bubble
    // appearing does not, if the user has scrolled up to read.  [M14 P10.7]
    scrollToBottom(role === 'user');
    return content;
}

/* ---------------------------------------------------------------------------
 * Copy button on assistant replies.  [M14 P10.7]
 *
 * The raw markdown is stashed on the element as a PROPERTY, never as a data-
 * attribute: replies run to thousands of characters, and an attribute would be
 * serialised into the DOM, doubling the memory for every message and showing
 * up in every inspection. A property is invisible to the serialiser.
 *
 * Copies the SOURCE, not the rendered text, so pasting into another markdown
 * surface keeps the headings, lists and code fences.
 * ------------------------------------------------------------------------- */

export function attachCopyButton(contentEl, raw) {
    if (!contentEl || !raw) return;
    contentEl._rawMarkdown = raw;                 // property, not attribute
    let btn = contentEl.querySelector('.msg-copy-btn');
    if (btn) return btn;                          // already attached, text updated above

    btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'msg-copy-btn';
    btn.title = 'Copy this reply';
    btn.setAttribute('aria-label', 'Copy this reply');
    btn.appendChild(svgIcon('copy'));

    let resetTimer = null;
    btn.addEventListener('click', async () => {
        const text = contentEl._rawMarkdown || '';
        if (!text) return;
        let ok = false;
        try {
            await navigator.clipboard.writeText(text);
            ok = true;
        } catch (_) {
            // Clipboard access is denied on insecure origins and in some
            // embedded webviews. Fall back rather than failing silently.
            try {
                const ta = document.createElement('textarea');
                ta.value = text;
                ta.setAttribute('readonly', '');
                ta.style.position = 'fixed';
                ta.style.opacity = '0';
                document.body.appendChild(ta);
                ta.select();
                ok = document.execCommand('copy');
                ta.remove();
            } catch (_) { ok = false; }
        }
        btn.classList.toggle('copied', ok);
        btn.replaceChildren(svgIcon(ok ? 'check' : 'warn'));
        btn.setAttribute('aria-label', ok ? 'Copied' : 'Could not copy');
        clearTimeout(resetTimer);
        resetTimer = setTimeout(() => {
            btn.classList.remove('copied');
            btn.replaceChildren(svgIcon('copy'));
            btn.setAttribute('aria-label', 'Copy this reply');
        }, 1400);
    });

    contentEl.appendChild(btn);
    return btn;
}

// Verification finishes after the reply has already streamed, so a FAIL lands
// once Jarvis has said "done". Showing it only in the side activity panel meant
// the user was never actually told the action had not worked.
export function addCorrectionMessage(text) {
    if (!text) return null;
    hideWelcome();
    const msg = document.createElement('div');
    msg.className = 'message assistant correction';
    const avatar = document.createElement('div');
    avatar.className = 'msg-avatar';
    avatar.innerHTML = AVATAR_ICON_ASSISTANT;
    const body = document.createElement('div');
    body.className = 'msg-body';
    const label = document.createElement('div');
    label.className = 'msg-label';
    label.textContent = 'Jarvis  (correction)';
    const content = document.createElement('div');
    content.className = 'msg-content';
    content.textContent = text;
    body.appendChild(label);
    body.appendChild(content);
    msg.appendChild(avatar);
    msg.appendChild(body);
    chatMessages.appendChild(msg);
    scrollToBottom();
    return content;
}

export function addTypingIndicator() {
    hideWelcome();
    const msg = document.createElement('div');
    msg.className = 'message assistant';
    msg.id = 'typing-msg';
    const avatar = document.createElement('div');
    avatar.className = 'msg-avatar';
    avatar.innerHTML = AVATAR_ICON_ASSISTANT;
    const body = document.createElement('div');
    body.className = 'msg-body';
    const label = document.createElement('div');
    label.className = 'msg-label';
    label.textContent = `Jarvis  (${state.currentMode === 'jarvis' ? 'Jarvis' : state.currentMode === 'realtime' ? 'Realtime' : 'General'})`;
    const content = document.createElement('div');
    content.className = 'msg-content';
    content.innerHTML = '<span class="msg-stream-text">...</span>';
    body.appendChild(label);
    body.appendChild(content);
    msg.appendChild(avatar);
    msg.appendChild(body);
    chatMessages.appendChild(msg);
    scrollToBottom();
    return content;
}

export function removeTypingIndicator() {
    const t = document.getElementById('typing-msg');
    if (t) t.remove();
}

/* How close to the bottom still counts as "following the stream".  [M14 P10.7]
 * Roughly one line of text: enough to survive the sub-pixel rounding that
 * makes scrollTop + clientHeight land a fraction short of scrollHeight, but
 * small enough that a deliberate scroll of even one wheel notch opts out. */
const SCROLL_ANCHOR_PX = 80;

/** True when the thread is parked at (or within a line of) the bottom. */
export function isNearBottom(el = chatMessages) {
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight <= SCROLL_ANCHOR_PX;
}

/**
 * Scroll the thread to the bottom.
 *
 * Unforced calls only move the view if the user was ALREADY at the bottom.
 * This used to fire unconditionally on every streamed chunk, which made it
 * impossible to scroll up and re-read anything while a reply was arriving -
 * the view snapped back inside the same frame. Pass `force` for the cases
 * where the user's own action justifies moving them: sending a message,
 * opening a conversation, pressing the scroll button.  [M14 P10.7]
 */
export function scrollToBottom(force = false) {
    if (!chatMessages) return;
    if (!force && !isNearBottom()) return;
    requestAnimationFrame(() => {
        chatMessages.scrollTop = chatMessages.scrollHeight;
    });
}
export let stopRequestedByUser = false;
export let activeStreamController = null;
export let lastUserText = '';

// Mark a half-written bubble so the user can tell "Jarvis finished" apart from
// "the wire died mid-sentence".
export function markStreamIncomplete(contentEl, kind) {
    if (!contentEl) return;
    const textSpan = contentEl.querySelector('.msg-stream-text');
    if (textSpan && textSpan.textContent === '...') textSpan.textContent = '';
    contentEl.classList.add('truncated', kind);
    const note = document.createElement('div');
    note.className = 'msg-incomplete' + (kind === 'stopped' ? ' neutral' : '');
    note.setAttribute('role', 'status');
    note.textContent = STREAM_INCOMPLETE_LABEL[kind] || STREAM_INCOMPLETE_LABEL.lost;
    contentEl.appendChild(note);
    scrollToBottom();
}

export function friendlyStreamError(err, idleTimedOut, totalTimedOut) {
    if (idleTimedOut) return 'No response for 45 seconds \u2014 the connection appears to have dropped.';
    if (totalTimedOut) return 'The request took too long and was cancelled.';
    const m = (err && err.message) || '';
    if (m.includes('503')) return 'Service temporarily unavailable. Please try again in a moment.';
    if (m.includes('429')) return 'Rate limit reached. Please wait a moment before trying again.';
    if (m.includes('Failed to fetch') || m.includes('NetworkError')) return 'Could not reach the server. Check that Jarvis is running.';
    if (m) return m.length > 140 ? m.slice(0, 137) + '...' : m;
    return 'Something went wrong. Please try again.';
}

// One recovery row: the reason, plus Retry (nothing arrived) or Regenerate
// (a partial answer is on screen and stays there).
export function appendStreamError(message, isRegenerate) {
    const wrap = document.createElement('div');
    wrap.className = 'msg-error';
    wrap.setAttribute('role', 'status');
    const txt = document.createElement('span');
    txt.className = 'msg-error-text';
    txt.textContent = message;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'msg-error-retry';
    btn.textContent = isRegenerate ? 'Regenerate' : 'Retry';
    btn.addEventListener('click', () => {
        if (state.isStreaming) return;
        btn.disabled = true;
        const t = lastUserText;
        wrap.remove();
        if (t) sendMessage(t);
    });
    wrap.appendChild(txt);
    wrap.appendChild(btn);
    if (chatMessages) chatMessages.appendChild(wrap);
    scrollToBottom();
}

// Send and Stop share one slot: exactly one is ever visible.
export function toggleSendStop(streaming) {
    if (sendBtn) sendBtn.hidden = !!streaming;
    if (stopBtn) stopBtn.hidden = !streaming;
}

export function anyDialogOpen() {
    const backdrop = document.getElementById('history-dialog-backdrop');
    if (backdrop && !backdrop.hidden && getComputedStyle(backdrop).display !== 'none') return true;
    const panel = document.getElementById('settings-panel');
    if (panel && panel.classList.contains('open')) return true;
    return false;
}

// Single funnel for Stop: the button, and Escape.
export function requestStopStreaming() {
    if (!state.isStreaming || !activeStreamController) return false;
    stopRequestedByUser = true;
    try { activeStreamController.abort(); } catch (_) {}
    return true;
}

// M14 P5: the single send path.
// imageOverride carries an already-captured frame (camera auto-capture flow).
// When it is present the camera is not touched again: the frame is simply
// attached, so text-only, live-vision and pre-captured sends share one
// lifecycle, one abort controller and one recovery path.
export async function sendMessage(textOverride, imageOverride) {
    let text = (textOverride || messageInput.value).trim();
    const hasPresetImage = typeof imageOverride === 'string' && imageOverride.length > 0;
    const visionModeOn = !hasPresetImage && camVisionModeInput && camVisionModeInput.checked;
    const wantsCamera = hasPresetImage || visionModeOn || isCameraQuery(text) || (state.camStream && text);
    if (wantsCamera && !text) text = 'What do you see?';
    if (!text || state.isStreaming) return;
    // User is interrupting the startup greeting by typing a command — kill it for good.
    stopStartupBrief();
    if (!hasPresetImage && (isCameraQuery(text) || visionModeOn) && !state.camStream) {
        try {
            await startCamera();
            await new Promise((resolve) => {
                if (!camVideo) { resolve(); return; }
                if (camVideo.readyState >= 2 && camVideo.videoWidth > 0) { resolve(); return; }
                const onReady = () => { camVideo.removeEventListener('loadeddata', onReady); clearTimeout(t); resolve(); };
                const t = setTimeout(() => { camVideo.removeEventListener('loadeddata', onReady); resolve(); }, 3000);
                camVideo.addEventListener('loadeddata', onReady);
            });
        } catch (_) {
        }
    }
    let imgBase64 = hasPresetImage ? imageOverride : null;
    if (!imgBase64 && state.camStream && wantsCamera) {
        imgBase64 = await captureFrameAsBase64Safe();
        if (!imgBase64) showToast('Camera frame not ready. Please try again.');
    }
    if (!textOverride) {
        // Only a genuinely typed message owns the composer contents.
        messageInput.value = '';
        autoResizeInput();
        charCount.textContent = '';
    }
    addMessage('user', text);
    addTypingIndicator();
    state.isStreaming = true;
    // Tells assistive technology the region is mid-update, so a screen reader
    // does not read half a sentence as the finished answer.  [M14 P10.7]
    if (chatMessages) chatMessages.setAttribute('aria-busy', 'true');
    emit(EVENTS.TURN_START, { sessionId: state.sessionId });
    // [M14 P7.1] Per-turn poller. Must come after `isStreaming = true` so the
    // idle counter cannot advance during the turn it was started for.
    startBackgroundActivityPolling();
    if (sendBtn) sendBtn.disabled = true;
    if (messageInput) messageInput.disabled = true;
    if (orbContainer) orbContainer.classList.add('active');
    if (state.orb) state.orb.setState('thinking');
    if (state.ttsPlayer) { state.ttsPlayer.reset(); state.ttsPlayer.unlock(); }
    const messageToSend = imgBase64 ? (text + ' ' + CAM_BYPASS_TOKEN) : text;
    const endpoint = '/chat/jarvis/stream';
    if (activityList) {
        activityList.innerHTML = '<div class="activity-empty" id="activity-empty">Processing...</div>';
        // Show voice input source if this message came from PTT
        if (state.pttVoiceSource) {
            appendActivity({ event: 'voice_input', source: state.pttVoiceSource });
            state.pttVoiceSource = null;
        }
        if (activityToggle) activityToggle.style.display = '';
        if (activityPanel && state.settings.autoOpenActivity) { activityPanel.classList.add('open'); updatePanelOverlay(); }
    }
    lastUserText = text;
    stopRequestedByUser = false;
    let firstChunkReceived = false;
    let sawAnyChunk = false;
    let idleTimedOut = false;
    let totalTimedOut = false;
    let timeoutId = null;
    let idleTimerId = null;
    // Hoisted so catch/finally can still reach the bubble and the cursor no
    // matter where in the read loop the stream died.
    let contentEl = null;
    let cursorEl = null;
    let fullResponse = '';
    const controller = new AbortController();
    state.pttStreamController = controller;  // expose to PTT for interrupt
    activeStreamController = controller;
    toggleSendStop(true);
    // Rearmed on every received byte: catches a socket that is open but silent.
    const armIdleTimer = () => {
        if (idleTimerId) clearTimeout(idleTimerId);
        idleTimerId = setTimeout(() => {
            idleTimedOut = true;
            try { controller.abort(); } catch (_) {}
        }, STREAM_IDLE_TIMEOUT_MS);
    };
    try {
        // Starter audio is now triggered by the 'decision' activity event
        // when query_type is 'realtime' (Serper search needed)
        timeoutId = setTimeout(() => { totalTimedOut = true; try { controller.abort(); } catch (_) {} }, STREAM_TOTAL_TIMEOUT_MS);
        armIdleTimer();
        const res = await fetch(`${API}${endpoint}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: messageToSend,
                session_id: state.sessionId,
                tts: !!(state.ttsPlayer && state.ttsPlayer.enabled),
                imgbase64: imgBase64 || null
            }),
            signal: controller.signal,
        });
        if (!res.ok) {
            let errMsg = `HTTP ${res.status}`;
            try {
                const err = await res.json();
                errMsg = err.detail || (Array.isArray(err.detail) ? err.detail.map(d => d.msg || d.loc?.join('.')).join('; ') : err.message) || errMsg;
            } catch (_) {}
            throw new Error(errMsg);
        }
        removeTypingIndicator();
        contentEl = addMessage('assistant', '');
        contentEl.innerHTML = '<span class="msg-stream-text">...</span>';
        scrollToBottom();
        if (!res.body) throw new Error('No response body');
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let sseBuffer = '';
        let streamDone = false;
        while (!streamDone) {
            const { done, value } = await reader.read();
            if (done) break;
            armIdleTimer();
            sseBuffer += decoder.decode(value, { stream: true });
            const lines = sseBuffer.split('\n\n');
            sseBuffer = lines.pop();
            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                try {
                    const data = JSON.parse(line.slice(6));
                    if (data.session_id) setActiveSession(data.session_id);
                    if (data.activity) {
                        appendActivity(data.activity);
                        if (activityToggle) activityToggle.style.display = '';
                        if (activityPanel && state.settings.autoOpenActivity) { activityPanel.classList.add('open'); updatePanelOverlay(); }
                        // Play starter audio ONLY when brain decides this is a realtime (web search) query
                        if (data.activity.event === 'decision' && data.activity.query_type === 'realtime') {
                            if (state.orb) state.orb.setState('searching');
                            if (state.ttsPlayer?.enabled && state.settings.thinkingSounds && state.preStarterPlayer) {
                                state.preStarterPlayer.play(() => {
                                });
                            }
                        }
                        // ── Orb state transitions from SSE activity events ──
                        if (data.activity.event === 'decision' && data.activity.query_type === 'task') {
                            if (state.orb) state.orb.setState('working');
                        }
                        if (data.activity.event === 'searching_web' || data.activity.event === 'extracting_query') {
                            if (state.orb) state.orb.setState('searching');
                        }
                        if (data.activity.event === 'tool_call') {
                            if (state.orb) state.orb.setState('working');
                        }
                        if (data.activity.event === 'first_chunk') {
                            if (state.orb && !(state.ttsPlayer && state.ttsPlayer.enabled)) state.orb.setState('speaking');
                        }
                        if (data.activity.event === 'agent_done' || data.activity.event === 'execution_completed') {
                            // Only go to speaking if TTS is active, otherwise wait for stream done
                            if (state.orb && state.ttsPlayer && state.ttsPlayer.enabled && state.ttsPlayer.playing) state.orb.setState('speaking');
                        }
                    }
                    if (data.search_results) {
                        renderSearchResults(data.search_results);
                        if (searchResultsToggle) searchResultsToggle.style.display = '';
                        if (searchResultsWidget && state.settings.autoOpenSearchResults) { searchResultsWidget.classList.add('open'); updatePanelOverlay(); }
                    }
                    if (data.actions) {
                        handleActions(data.actions, contentEl);
                    }
                    if ('chunk' in data) {
                        const chunkText = data.chunk || '';
                        if (chunkText && !firstChunkReceived) {
                            firstChunkReceived = true;
                            // Stop starter audio immediately for clean handoff to actual TTS
                            if (state.preStarterPlayer && state.preStarterPlayer.audio) {
                                state.preStarterPlayer.audio.pause();
                                state.preStarterPlayer.audio.currentTime = 0;
                            }
                            if (state.ttsPlayer) state.ttsPlayer.reset();
                        }
                        fullResponse += chunkText;
                        sawAnyChunk = true;
                        if (!cursorEl) {
                            cursorEl = document.createElement('span');
                            cursorEl.className = 'stream-cursor';
                            cursorEl.textContent = '|';
                            contentEl.appendChild(cursorEl);
                        }
                        // streaming=true, so the partial document is repaired
                        // first: no bold that switches on halfway through a
                        // word, no fence left hanging open. [M14 P6]
                        scheduleMarkdownRender(contentEl, fullResponse, true, cursorEl);
                        scrollToBottom();
                    }
                    if (data.audio && state.ttsPlayer) {
                        state.ttsPlayer.enqueue(data.audio);
                    }
                    if (data.error) throw new Error(data.error);
                    if (data.done) { streamDone = true; break; }
                } catch (parseErr) {
                    if (parseErr.message && !parseErr.message.includes('JSON'))
                        throw parseErr;
                }
            }
            if (streamDone) break;
        }
        if (cursorEl) { cursorEl.remove(); cursorEl = null; }
        // Final pass with streaming=false: no repair, so the last thing left on
        // screen is exactly what the model actually said. [M14 P6]
        cancelMarkdownRender();
        if (fullResponse) {
            renderMarkdownInto(contentEl, fullResponse, false, null);
            // Only once the reply is complete: copying a half-streamed answer
            // would quietly hand the user a truncated document.  [M14 P10.7]
            attachCopyButton(contentEl, fullResponse);
        } else {
            const textSpan = contentEl.querySelector('.msg-stream-text');
            if (textSpan) textSpan.textContent = '(No response)';
        }
    } catch (err) {
        clearTimeout(timeoutId);
        if (idleTimerId) clearTimeout(idleTimerId);
        removeTypingIndicator();
        const aborted = !!(err && err.name === 'AbortError');
        if (aborted && stopRequestedByUser) {
            // Class 1: the user pressed Stop. Everything already streamed stays.
            markStreamIncomplete(contentEl, 'stopped');
        } else if (aborted && state.isRecording && !idleTimedOut && !totalTimedOut) {
            // Class 2: PTT interrupt. Stay silent, the user is talking over Jarvis.
            markStreamIncomplete(contentEl, 'stopped');
        } else if (sawAnyChunk) {
            // Class 3a: died mid-answer. Keep the partial text, offer Regenerate.
            markStreamIncomplete(contentEl, 'lost');
            appendStreamError(friendlyStreamError(err, idleTimedOut, totalTimedOut), true);
        } else {
            // Class 3b: nothing ever arrived, so there is nothing to preserve.
            appendStreamError(friendlyStreamError(err, idleTimedOut, totalTimedOut), false);
        }
    } finally {
        // Runs on every path, including Stop and a mid-stream death, so the
        // composer can never be left disabled behind a blinking cursor.
        // A queued markdown frame must not fire after the bubble has been
        // marked incomplete, or it would repaint over that marker. [M14 P6]
        cancelMarkdownRender();
        clearTimeout(timeoutId);
        if (idleTimerId) clearTimeout(idleTimerId);
        if (cursorEl) { cursorEl.remove(); cursorEl = null; }
        state.isStreaming = false;
        // In `finally`, so a crashed or stopped turn can never strand the
        // thread as permanently busy.  [M14 P10.7]
        if (chatMessages) chatMessages.setAttribute('aria-busy', 'false');
        emit(EVENTS.TURN_END, { sessionId: state.sessionId });
        state.pttStreamController = null;
        activeStreamController = null;
        toggleSendStop(false);
        if (sendBtn) sendBtn.disabled = false;
        if (messageInput) messageInput.disabled = false;
        if (orbContainer) orbContainer.classList.remove('active');
        // Only reset to idle if TTS isn't actively playing
        if (state.orb && !(state.ttsPlayer && state.ttsPlayer.playing)) state.orb.setState('idle');
    }
}


/* Event wiring that used to sit at the top level of script.js. Under ES
 * modules it cannot stay there: it touches elements owned by other
 * modules, and in an import cycle those bindings are still in their
 * temporal dead zone while this module is being evaluated. main.js calls
 * this once every module exists, in the original source order.
 *   [M14 P9.3] */
export function initWiring() {
    /* M14 P3: Stop control. Button and Escape share one funnel; Escape is ignored
       while a dialog is open so it keeps its normal close-the-dialog job. */
    if (stopBtn) {
        stopBtn.hidden = true;
        stopBtn.addEventListener('click', () => { requestStopStreaming(); });
    }
}


/* ---------------------------------------------------------------------------
 * [M14 P9.3 moved from main.js]
 *
 * These touch the composer and the welcome block - DOM this module already
 * owns - and they were the only reason chat.js and history.js imported the
 * entry module. index.html loads main.js with a ?v= cache-buster, so those
 * imports resolved to a second URL and the browser built a SECOND main.js,
 * whose body ran mid-graph and read bindings that did not exist yet.
 * Nothing imports the entry now.
 * ------------------------------------------------------------------------- */

export const welcomeTitle = $('welcome-title');


export function setGreeting() {
    const h = new Date().getHours();
    let g = 'Good evening.';
    if (h < 12) g = 'Good morning.';
    else if (h < 17) g = 'Good afternoon.';
    else if (h >= 22) g = 'Burning the midnight oil?';
    if (welcomeTitle) welcomeTitle.textContent = g;
}

export function autoResizeInput() {
    messageInput.style.height = 'auto';
    messageInput.style.height = Math.min(messageInput.scrollHeight, 120) + 'px';
}

export function newChat(opts = {}) {
    if (state.isRecording) pttStopRecording();
    if (state.ttsPlayer) state.ttsPlayer.stop();
    if (state.camStream) stopCamera();
    // The previous conversation stays on disk and in History; only the active
    // pointer is cleared. The new chat has no id until the first reply comes
    // back, so the URL drops back to the base path.
    state.sessionId = null;
    syncUrl(null, opts.urlMode || 'push');
    markActiveHistoryItem();
    if (chatMessages) chatMessages.innerHTML = '';
    chatMessages.appendChild(createWelcome());
    messageInput.value = '';
    autoResizeInput();
    setGreeting();
    if (searchResultsWidget) searchResultsWidget.classList.remove('open');
    if (searchResultsToggle) searchResultsToggle.style.display = 'none';
    if (activityPanel) activityPanel.classList.remove('open');
    if (settingsPanel) settingsPanel.classList.remove('open');
    if (activityToggle) activityToggle.style.display = 'none';
    if (activityList) {
        activityList.innerHTML = '<div class="activity-empty" id="activity-empty">Send a message to see the flow here.</div>';
    }
    updatePanelOverlay();
}

export function createWelcome() {
    const h = new Date().getHours();
    let g = 'Good evening.';
    if (h < 12) g = 'Good morning.';
    else if (h < 17) g = 'Good afternoon.';
    else if (h >= 22) g = 'Burning the midnight oil?';
    const div = document.createElement('div');
    div.className = 'welcome-screen';
    div.id = 'welcome-screen';
    div.innerHTML = `
        <div class="welcome-icon">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
        </div>
        <h2 class="welcome-title">${g}</h2>
        <p class="welcome-sub">How may I assist you today?</p>
        <div class="welcome-chips">
            <button class="chip" data-msg="What can you do?">What can you do?</button>
            <button class="chip" data-msg="Open YouTube for me">Open YouTube</button>
            <button class="chip" data-msg="Tell me a fun fact">Fun fact</button>
            <button class="chip" data-msg="Play some music">Play music</button>
        </div>`;
    div.querySelectorAll('.chip').forEach(c => {
        c.addEventListener('click', () => { if (!state.isStreaming) sendMessage(c.dataset.msg); });
    });
    return div;
}
