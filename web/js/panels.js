/* ---------------------------------------------------------------------------
 * panels.js
 *
 * Panel chrome: overlay, drag / resize, toasts, activity, search results.
 *
 * Extracted from the single 4000-line script.js by /data/p9_3b.py.
 * [M14 P9.3]
 * ------------------------------------------------------------------------- */

import { EVENTS, emit } from './bus.js';
import { addCorrectionMessage } from './chat.js';
import { ACTIVITY_POLL_MAX_IDLE_TICKS, ACTIVITY_POLL_MS, ACTIVITY_STEPS, API, FLOAT_PANEL_SELECTOR, INERT_PANEL_SELECTOR, SETTINGS_KEY, TOAST_MAX_VISIBLE } from './config.js';
import { $, el } from './dom.js';
import { restorePanelGeometry, savePanelGeometry } from './geometry.js';
import { notesPanel, notesPanelHeader } from './notes.js';
import { orbDashLoad, orbDashboard, updateOrbOcclusion } from './orbctl.js';
import { remindersPanel, remindersPanelHeader } from './reminders.js';
import { state } from './state.js';
import { escapeAttr, escapeHtml, friendlyUrlLabel, isUrlLike, safeUrlForHref, truncateSnippet } from './util.js';
export const backgroundActivitySeen = new Set();
export const backgroundActivityStartedAt = Date.now() / 1000;
export const searchResultsToggle = $('search-results-toggle');
export const searchResultsWidget = $('search-results-widget');
export const searchResultsClose  = $('search-results-close');
export const searchResultsQuery  = $('search-results-query');
export const searchResultsAnswer = $('search-results-answer');
export const searchResultsList   = $('search-results-list');
export const activityPanel       = $('activity-panel');
export const activityToggle      = $('activity-toggle');
export const activityClose       = $('activity-close');
export const activityList        = $('activity-list');
export const panelOverlay        = $('panel-overlay');
export const settingsBtn         = $('settings-btn');
export const monitorBtn          = $('monitor-btn');
export const settingsPanel       = $('settings-panel');
export const settingsClose       = $('settings-close');
export const toggleAutoActivity  = $('toggle-auto-activity');
export const toggleAutoSearch    = $('toggle-auto-search');
export const toggleThinkingSounds = $('toggle-thinking-sounds');
export const toastContainer     = $('toast-container');

/* ---------------------------------------------------------------------------
 * Toasts.  [M14 P10.6]
 *
 * Previously every call appended another box and started another timer, so a
 * burst of errors buried the screen and the first message scrolled away before
 * anyone could read it. Now:
 *
 *   - at most TOAST_MAX_VISIBLE are shown, the rest queue,
 *   - the queue is summarised by one "+N more" line rather than silently
 *     dropped, because a swallowed error message is worse than a late one,
 *   - the dismiss countdown PAUSES while the pointer is over a toast or focus
 *     is inside it. Reading a message should not be a race against a timer.
 * ------------------------------------------------------------------------- */

const toastQueue = [];
const liveToasts = new Set();
let toastOverflowEl = null;

function syncToastOverflow() {
    if (!toastContainer) return;
    if (!toastQueue.length) {
        if (toastOverflowEl) { toastOverflowEl.remove(); toastOverflowEl = null; }
        return;
    }
    if (!toastOverflowEl) {
        toastOverflowEl = document.createElement('div');
        toastOverflowEl.className = 'toast toast-overflow toast-visible';
        // polite, not assertive: it is a running count, not news.
        toastOverflowEl.setAttribute('role', 'status');
        toastContainer.appendChild(toastOverflowEl);
    }
    toastOverflowEl.textContent = '+' + toastQueue.length + ' more';
    toastContainer.appendChild(toastOverflowEl);   // keep it last
}

function dismissToast(entry) {
    if (!liveToasts.has(entry)) return;
    liveToasts.delete(entry);
    clearTimeout(entry.timer);
    entry.el.classList.remove('toast-visible');
    setTimeout(() => {
        entry.el.remove();
        pumpToasts();
    }, 300);
}

function startToastTimer(entry) {
    clearTimeout(entry.timer);
    entry.startedAt = Date.now();
    entry.timer = setTimeout(() => dismissToast(entry), entry.remaining);
}

function pauseToastTimer(entry) {
    if (!entry.timer) return;
    clearTimeout(entry.timer);
    entry.timer = null;
    entry.remaining = Math.max(600, entry.remaining - (Date.now() - entry.startedAt));
}

function showOneToast(msg, durationMs) {
    const el = document.createElement('div');
    el.className = 'toast';
    el.textContent = msg;
    el.tabIndex = 0;                       // focusable, so it can be read and paused
    el.setAttribute('role', 'status');
    toastContainer.appendChild(el);
    el.offsetHeight;                       // force a frame so the transition runs
    el.classList.add('toast-visible');

    const entry = { el, remaining: durationMs, timer: null, startedAt: 0 };
    liveToasts.add(entry);
    startToastTimer(entry);

    el.addEventListener('mouseenter', () => pauseToastTimer(entry));
    el.addEventListener('focusin', () => pauseToastTimer(entry));
    const resume = () => { if (!el.matches(':hover') && !el.contains(document.activeElement)) startToastTimer(entry); };
    el.addEventListener('mouseleave', resume);
    el.addEventListener('focusout', resume);
    el.addEventListener('click', () => dismissToast(entry));
    el.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ' || e.key === 'Escape') { e.preventDefault(); dismissToast(entry); }
    });
    syncToastOverflow();
}

function pumpToasts() {
    while (liveToasts.size < TOAST_MAX_VISIBLE && toastQueue.length) {
        const next = toastQueue.shift();
        showOneToast(next.msg, next.durationMs);
    }
    syncToastOverflow();
}

export function showToast(msg, durationMs = 5000) {
    if (!toastContainer || !msg) return;
    if (liveToasts.size >= TOAST_MAX_VISIBLE) {
        toastQueue.push({ msg, durationMs });
        syncToastOverflow();
        return;
    }
    showOneToast(msg, durationMs);
}

/** Visible toast count and queue depth - for tests and for the panel probe. */
export function toastState() {
    return { visible: liveToasts.size, queued: toastQueue.length };
}

/**
 * Close `panel` after `ms`, unless the user touches it first.
 *
 * Returns a cancel function. Only ever used for panels the AGENT opened; a
 * panel the user opened has no business closing itself.  [M14 P10.6]
 */
export function armAutoClose(panel, ms, closeFn) {
    if (!panel || !ms) return () => {};
    let timer = setTimeout(() => { cancel(); closeFn(); }, ms);

    function cancel() {
        if (!timer) return;
        clearTimeout(timer);
        timer = null;
        panel.removeEventListener('pointerdown', cancel, true);
        panel.removeEventListener('keydown', cancel, true);
        panel.removeEventListener('focusin', cancel, true);
        panel.removeEventListener('wheel', cancel, true);
    }

    // Capture phase: the panel's own handlers may stop propagation, and a
    // click that never reaches this listener would leave the panel closing
    // under the user's hand.
    panel.addEventListener('pointerdown', cancel, true);
    panel.addEventListener('keydown', cancel, true);
    panel.addEventListener('focusin', cancel, true);
    panel.addEventListener('wheel', cancel, true);
    return cancel;
}

export function updatePanelOverlay() {
    if (panelOverlay) {
        const anyOpen = (activityPanel && activityPanel.classList.contains('open')) ||
            (searchResultsWidget && searchResultsWidget.classList.contains('open')) ||
            (settingsPanel && settingsPanel.classList.contains('open')) ||
            (orbDashboard && orbDashboard.classList.contains('open'));
        panelOverlay.classList.toggle('visible', !!anyOpen);
    }
    updateOrbOcclusion();
}

export function renderSearchResults(payload) {
    if (!payload) return;
    if (searchResultsQuery) searchResultsQuery.textContent = (payload.query || '').trim() || 'Search';
    if (searchResultsAnswer) searchResultsAnswer.textContent = (payload.answer || '').trim() || '';
    if (!searchResultsList) return;
    searchResultsList.innerHTML = '';
    const results = payload.results || [];
    const maxContentLen = 220;
    for (const r of results) {
        let title = (r.title || '').trim();
        let content = (r.content || '').trim();
        const url = (r.url || '').trim();
        if (isUrlLike(title)) title = friendlyUrlLabel(url) || 'Source';
        if (!title) title = friendlyUrlLabel(url) || 'Source';
        if (isUrlLike(content)) content = '';
        content = truncateSnippet(content, maxContentLen);
        const score = r.score != null ? Math.round((r.score || 0) * 100) : null;
        const card = document.createElement('div');
        card.className = 'search-result-card';
        const urlDisplay = url ? escapeHtml(friendlyUrlLabel(url)) : '';
        const hrefSafe = safeUrlForHref(url);
        const urlMarkup = urlDisplay
            ? (hrefSafe ? `<a href="${hrefSafe}" target="_blank" rel="noopener" class="card-url" title="${escapeAttr(url)}">${urlDisplay}</a>` : `<span class="card-url">${urlDisplay}</span>`)
            : '';
        card.innerHTML = `
            <div class="card-title">${escapeHtml(title)}</div>
            ${content ? `<div class="card-content">${escapeHtml(content)}</div>` : ''}
            ${urlMarkup}
            ${score != null ? `<div class="card-score">Relevance: ${escapeHtml(String(score))}%</div>` : ''}`;
        searchResultsList.appendChild(card);
    }
}

export function appendActivity(activity) {
    if (!activityList || !activity) return;
    emit(EVENTS.ACTIVITY_ROW, activity);
    if (activity.verdict === 'FAIL') emit(EVENTS.VERDICT_FAIL, activity);
    const item = document.createElement('div');
    item.className = 'activity-item';
    item.setAttribute('data-event', activity.event || '');
    const stepInfo = ACTIVITY_STEPS[activity.event] || { step: 0, label: activity.event || 'Activity' };
    let detail = '';
    const addRouteClass = (route) => {
        if (route === 'general') item.classList.add('route-general');
        else if (route === 'realtime') item.classList.add('route-realtime');
        else if (route === 'vision' || route === 'camera') item.classList.add('route-vision');
        else if (route === 'task' || route === 'mixed') item.classList.add('route-task');
        else if (route === 'chat') item.classList.add('route-chat');
    };
    const fmtTime = (ms) => ms != null ? (ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`) : '';

    if (activity.event === 'voice_input') {
        if (activity.source === 'web-speech') {
            detail = 'via Web Speech API (instant)';
        } else if (activity.source === 'whisper') {
            detail = 'via Groq Whisper (fallback)';
        } else {
            detail = 'Voice input';
        }
        item.classList.add('activity-sub');
    } else if (activity.event === 'query_detected') {
        detail = activity.message || '';
    } else if (activity.event === 'decision') {
        // `query_type` is the ROUTE (task/general/realtime/mixed) so the orb and
        // route colours keep working; `kind` is the resolver's own word (M13).
        const cat = (activity.query_type || '?').charAt(0).toUpperCase() + (activity.query_type || '').slice(1);
        const kind = activity.kind ? ` · ${activity.kind.replace(/_/g, ' ')}` : '';
        const t = fmtTime(activity.elapsed_ms);
        detail = (t ? `${cat} (${t})` : cat) + kind;
        addRouteClass(activity.query_type);
    } else if (activity.event === 'intent_classified') {
        detail = (activity.intent || '?').charAt(0).toUpperCase() + (activity.intent || '').slice(1);
        item.classList.add('activity-sub', 'route-task');
    } else if (activity.event === 'routing') {
        const r = (activity.route || '?').charAt(0).toUpperCase() + (activity.route || '').slice(1);
        detail = `→ ${r}`;
        addRouteClass(activity.route);
    } else if (activity.event === 'streaming_started') {
        const r = (activity.route || '?').charAt(0).toUpperCase() + (activity.route || '').slice(1);
        detail = `via ${r}`;
        addRouteClass(activity.route);
    } else if (activity.event === 'first_chunk') {
        const t = fmtTime(activity.elapsed_ms);
        detail = t ? `in ${t}` : 'Started';
        addRouteClass(activity.route);
    } else if (activity.event === 'context_retrieved') {
        detail = 'Knowledge base';
        item.classList.add('activity-sub', 'route-general');
    } else if (activity.event === 'searching_web') {
        detail = activity.query ? `"${activity.query}"` : (activity.message || 'Searching...');
        item.classList.add('activity-sub', 'route-realtime');
    } else if (activity.event === 'search_completed') {
        detail = activity.message || 'Done';
        item.classList.add('activity-sub', 'route-realtime');
    } else if (activity.event === 'tasks_executing') {
        detail = activity.message || 'Running...';
        item.classList.add('activity-sub', 'route-task');
    } else if (activity.event === 'tasks_completed') {
        detail = activity.message || 'Done';
        item.classList.add('activity-sub', 'route-task');
    } else if (activity.event === 'vision_analyzing') {
        detail = 'Processing camera frame';
        item.classList.add('activity-sub', 'route-vision');
    } else if (activity.event === 'actions_emitted') {
        detail = activity.message || 'Sent';
        item.classList.add('activity-sub');
    } else if (activity.event === 'extracting_query') {
        detail = 'Parsing search terms';
        item.classList.add('activity-sub');
    } else if (activity.event === 'tts_cache_hit') {
        // Green-tinted sub-item: sentence served instantly from local SSD
        detail = activity.sentence ? `"${activity.sentence}"` : 'Instant playback';
        item.classList.add('activity-sub', 'route-tts-hit');
    } else if (activity.event === 'tts_cache_miss_saved') {
        // Blue-tinted sub-item: sentence downloaded online and now saved to disk
        detail = activity.sentence ? `"${activity.sentence}"` : 'Saved to cache';
        item.classList.add('activity-sub', 'route-tts-saved');
    } else if (activity.event === 'agent_started') {
        detail = activity.tools != null ? `${activity.tools} tools ready` : 'Agent started';
        item.classList.add('route-task');
    } else if (activity.event === 'execution_started') {
        detail = activity.execution_id ? `Run ${activity.execution_id}` : 'New execution';
        item.classList.add('activity-sub', 'route-task');
    } else if (activity.event === 'execution_completed') {
        detail = `${activity.ok === false ? 'Failed' : 'Completed'}`
            + (activity.path ? ` via ${activity.path} path` : '')
            + (activity.execution_id ? ` — ${activity.execution_id}` : '');
        item.classList.add('activity-sub', activity.ok === false ? 'route-realtime' : 'route-task');
    } else if (activity.event === 'confirmation_granted') {
        detail = `${activity.tool || 'Action'} approved for this exact request`;
        item.classList.add('activity-sub', 'route-task');
    } else if (activity.event === 'frontend_ack') {
        detail = activity.accepted ? 'Browser accepted the action'
            : `Action attempted${activity.error ? ` — ${activity.error}` : ''}`;
        item.classList.add('activity-sub', activity.accepted ? 'route-task' : 'route-realtime');
    } else if (activity.event === 'understood') {
        // M13 §4.6 — the resolved, self-contained goal, every turn.
        const t = fmtTime(activity.elapsed_ms);
        detail = `"${activity.goal || ''}"`
            + (activity.kind ? ` — ${activity.kind.replace(/_/g, ' ')}` : '')
            + (t ? ` (${t})` : '');
        if (activity.self_contained === false) item.classList.add('activity-sub');
    } else if (activity.event === 'verifying') {
        detail = activity.message
            || `${activity.actions || 0} action${activity.actions === 1 ? '' : 's'}`;
        item.classList.add('activity-sub', 'route-task');
    } else if (activity.event === 'verdict') {
        detail = `${activity.tool || 'action'}: ${activity.verdict || 'UNKNOWN'}`
            + (activity.reason ? ` — ${activity.reason}` : '');
        item.classList.add('activity-sub',
            activity.verdict === 'PASS' ? 'route-task' : 'route-realtime');
    } else if (activity.event === 'retrying') {
        detail = activity.reason || activity.message || 'Trying again';
        item.classList.add('activity-sub', 'route-realtime');
    } else if (activity.event === 'no_op_rejected') {
        detail = activity.message
            || (activity.said ? `Refused reply: "${activity.said}"` : 'Nothing ran');
        item.classList.add('activity-sub', 'route-realtime');
    } else if (activity.event === 'cache_miss') {
        detail = 'Miss — using normal agent path';
        item.classList.add('activity-sub');
    } else if (activity.event === 'cache_hit') {
        const kind = activity.kind || (activity.tool ? 'tool' : 'entry');
        const count = activity.steps ? `, ${activity.steps} steps` : '';
        detail = `Hit — ${kind}${count}`;
        item.classList.add('activity-sub', 'route-task');
    } else if (activity.event === 'cache_replay') {
        detail = `${activity.kind || 'entry'}, ${activity.steps || 1} step${activity.steps === 1 ? '' : 's'}`;
        item.classList.add('activity-sub', 'route-task');
    } else if (activity.event === 'verification_queued') {
        detail = `${activity.actions || 0} action${activity.actions === 1 ? '' : 's'} sent for checking`;
        item.classList.add('activity-sub');
    } else if (activity.event === 'verification_result') {
        detail = `${activity.tool || 'action'}: ${activity.verdict || 'UNKNOWN'}`
            + (activity.reason ? ` — ${activity.reason}` : '')
            + (activity.source ? ` (via ${activity.source})` : '');
        item.classList.add('activity-sub', activity.verdict === 'FAIL' ? 'route-realtime' : 'route-task');
    } else if (activity.event === 'cache_status') {
        detail = `${activity.action || 'update'}: ${activity.trigger || ''}`
            + (activity.detail ? ` (${activity.detail})` : '');
        item.classList.add('activity-sub', 'route-task');
    } else if (activity.event === 'tool_call') {
        let argStr = '';
        if (activity.args && typeof activity.args === 'object') {
            argStr = Object.entries(activity.args)
                .map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`)
                .join(', ');
        }
        if (argStr.length > 80) argStr = argStr.slice(0, 80) + '…';
        const stepTxt = activity.step != null ? `#${activity.step} ` : '';
        detail = `${stepTxt}${activity.tool || 'tool'}${argStr ? ` (${argStr})` : ''}`;
        item.classList.add('activity-sub', 'route-task');
    } else if (activity.event === 'tool_result') {
        const okTxt = activity.ok === false ? 'failed' : 'done';
        const preview = activity.preview ? ` — ${activity.preview}` : '';
        detail = `${activity.tool || 'tool'}: ${okTxt}${preview}`;
        item.classList.add('activity-sub', activity.ok === false ? 'route-realtime' : 'route-task');
    } else if (activity.event === 'awaiting_confirmation') {
        detail = activity.tool ? `${activity.tool} needs your confirmation` : 'Needs confirmation';
        item.classList.add('activity-sub', 'route-task');
    } else if (activity.event === 'agent_done') {
        detail = activity.steps != null ? `${activity.steps} step${activity.steps === 1 ? '' : 's'}` : 'Done';
        item.classList.add('route-task');
    } else if (activity.event === 'llm_provider' || activity.event === 'provider_failover' || activity.event === 'provider_race') {
        // Which LLM provider/key actually answered (Groq vs Gemini), incl. failover & race winner.
        detail = activity.message
            || (activity.key_label ? `${activity.provider || ''} (${activity.key_label})`.trim() : (activity.provider || 'Provider'));
        item.classList.add('activity-sub');
        if (activity.provider === 'gemini') item.classList.add('route-gemini');
        else if (activity.provider === 'groq') item.classList.add('route-groq');
        else if (activity.provider === 'zai') item.classList.add('route-zai');
        addRouteClass(activity.route);
    } else {
        detail = activity.message || '';
    }
    const stepNum = stepInfo.step ? `<span class="activity-step">${stepInfo.step}</span>` : '';
    item.innerHTML = `
        <div class="activity-event">${stepNum}${escapeHtml(stepInfo.label)}</div>
        <div class="activity-detail">${escapeHtml(detail || '')}</div>`;
    const emptyEl = activityList.querySelector('.activity-empty');
    if (emptyEl) emptyEl.style.display = 'none';
    activityList.appendChild(item);
    activityList.scrollTop = activityList.scrollHeight;
}

/* Activity polling.  [M14 P7.1]
   Was: `window.setInterval(poll, 2000)` with the handle thrown away, so it
   could never be cleared. `if (document.hidden) return` skipped the fetch but
   the timer still fired 30 times a minute for the entire life of the tab, on a
   machine that is also running an embedding model -- and it kept polling long
   after every verdict for the turn had already landed.

   Now: one stoppable timer, paused while the tab is hidden, and stopped once
   the turn has gone quiet. Verification is async but bounded (CHECKER_SETTLE_
   PROFILES, config.py:331), so polling forever was never necessary. */
export let activityPollTimer = null;
export let activityPollIdleTicks = 0;
// True once a turn has gone quiet and the poller has stopped itself. Without
// this the visibility handler would restart a finished turn's poller on every
// tab switch, because stopping resets the idle counter -- the leak this phase
// exists to close, reintroduced through the back door. Only a new turn clears
// it.
export let activityPollSettled = false;

export function stopBackgroundActivityPolling() {
    if (activityPollTimer) { clearInterval(activityPollTimer); activityPollTimer = null; }
    activityPollIdleTicks = 0;
}

/** Starts a fresh poll cycle for one turn. Safe to call repeatedly -- it
 *  always clears the previous timer first, so two pollers can never stack. */
export function startBackgroundActivityPolling() {
    stopBackgroundActivityPolling();
    activityPollIdleTicks = 0;
    activityPollSettled = false;      // a new turn is worth watching again
    backgroundActivitySeen.clear();   // [M14 P7.2] new turn, new key space
    activityPollTimer = setInterval(pollActivityOnce, ACTIVITY_POLL_MS);
    pollActivityOnce();
}

export async function pollActivityOnce() {
    // A hidden tab has no timer at all (see activityPollVisibilityChanged), but
    // guard anyway: a hidden tick must never be counted as an idle tick.
    if (document.hidden) return;
    const newRows = await fetchActivityRows();
    if (newRows > 0) {
        activityPollIdleTicks = 0;
    } else if (!state.isStreaming && ++activityPollIdleTicks >= ACTIVITY_POLL_MAX_IDLE_TICKS) {
        // The `!isStreaming` guard is load-bearing. A long agent turn is
        // legitimately silent between tool calls, and stopping the poller
        // mid-turn would mean FAIL verdicts never produce a correction bubble
        // (M5) -- a regression in truthfulness, not in polish.
        activityPollSettled = true;
        stopBackgroundActivityPolling();
    }
}

/** Fetches one batch and renders anything unseen.
 *  @returns {Promise<number>} how many rows were newly shown, so the caller
 *  can decide whether the turn has gone quiet. */
export async function fetchActivityRows() {
    let shown = 0;
    try {
        const response = await fetch(`${API}/api/activity/recent`, { cache: 'no-store' });
        if (!response.ok) return shown;
            const data = await response.json();
            const events = [];
            for (const row of (data.verification || [])) {
                if ((row.time || 0) < backgroundActivityStartedAt - 2) continue;
                events.push({
                    time: row.time || 0,
                    key: `v:${row.time}:${row.tool}:${row.verdict}:${row.reason}`,
                    activity: { event: 'verification_result', tool: row.tool,
                        verdict: row.verdict, reason: row.reason, source: row.source },
                    // Only FAIL surfaces in the conversation. PASS/UNKNOWN stay
                    // in the side panel, or every turn would be followed by noise.
                    correction: row.verdict === 'FAIL' ? (row.message || '') : ''
                });
            }
            for (const row of (data.cache || [])) {
                if ((row.time || 0) < backgroundActivityStartedAt - 2) continue;
                events.push({
                    time: row.time || 0,
                    key: `c:${row.time}:${row.action}:${row.trigger}:${row.detail}`,
                    activity: { event: 'cache_status', action: row.action,
                        trigger: row.trigger, detail: row.detail }
                });
            }
            events.sort((a, b) => a.time - b.time);
            for (const event of events) {
                if (backgroundActivitySeen.has(event.key)) continue;
                backgroundActivitySeen.add(event.key);
                shown++;
                appendActivity(event.activity);
                if (event.correction) addCorrectionMessage(event.correction);
            }
            // [M14 P7.2] Belt and braces for one pathologically long turn. A
            // single turn can emit a lot of rows (16 agent steps x tool +
            // verdict + cache); 2000 is far above any real turn and far below
            // "leak". Clearing mid-turn can re-show rows the panel already has
            // (appendActivity is append-only), which is the correct trade at a
            // size that should never be reached.
            if (backgroundActivitySeen.size > 2000) backgroundActivitySeen.clear();
    } catch (_) {
        // Background visibility is optional and must never affect chat.
    }
    return shown;
}

export function activityPollVisibilityChanged(hidden) {
    if (hidden) {
        // Stop the timer outright rather than firing and returning early.
        if (activityPollTimer) { clearInterval(activityPollTimer); activityPollTimer = null; }
        return;
    }
    if (activityPollTimer) return;
    if (activityPollSettled) return;   // finished turn: coming back must not revive it
    // Only resume if this turn still has something to wait for.
    if (state.isStreaming || activityPollIdleTicks < ACTIVITY_POLL_MAX_IDLE_TICKS) {
        activityPollTimer = setInterval(pollActivityOnce, ACTIVITY_POLL_MS);
    }
}

export function bringFloatPanelToFront(panel) {
    if (!panel) return;
    document.querySelectorAll(FLOAT_PANEL_SELECTOR).forEach(p => {
        if (p !== panel) p.style.zIndex = '';
    });
    panel.style.zIndex = 'calc(var(--z-float) + 1)';
}

export function initFloatPanelStacking() {
    document.addEventListener('mousedown', (e) => {
        const panel = e.target.closest?.(FLOAT_PANEL_SELECTOR);
        if (panel) bringFloatPanelToFront(panel);
    }, true);
}

export function makeDraggable(panel, header) {
    if (!panel || !header) return;
    let isDragging = false, startX, startY, origLeft, origTop;
    header.addEventListener('mousedown', e => {
        if (e.target.closest('.jarvis-panel-btn')) return;
        isDragging = true;
        const rect = panel.getBoundingClientRect();
        startX = e.clientX; startY = e.clientY;
        origLeft = rect.left; origTop = rect.top;
        panel.style.transition = 'none';
        document.addEventListener('mousemove', onDrag);
        document.addEventListener('mouseup', stopDrag);
        e.preventDefault();
    });
    function onDrag(e) {
        if (!isDragging) return;
        const dx = e.clientX - startX, dy = e.clientY - startY;
        panel.style.left = (origLeft + dx) + 'px';
        panel.style.top = (origTop + dy) + 'px';
        panel.style.right = 'auto';
        panel.style.bottom = 'auto';
    }
    function stopDrag() {
        if (!isDragging) return;
        isDragging = false;
        panel.style.transition = '';
        /* Saved here rather than in onDrag: a mousemove handler runs dozens of
           times a second and localStorage writes are synchronous, so persisting
           per frame would make the drag stutter. This also snaps the panel flush
           to a nearby edge.  [M14 P10.5] */
        savePanelGeometry(panel, false);
        document.removeEventListener('mousemove', onDrag);
        document.removeEventListener('mouseup', stopDrag);
    }
}

export function makeResizable(panel, handle) {
    if (!panel || !handle) return;
    let isResizing = false, startX, startY, origW, origH;
    handle.addEventListener('mousedown', e => {
        isResizing = true;
        startX = e.clientX; startY = e.clientY;
        origW = panel.offsetWidth; origH = panel.offsetHeight;
        panel.style.transition = 'none';
        document.addEventListener('mousemove', onResize);
        document.addEventListener('mouseup', stopResize);
        e.preventDefault();
    });
    function onResize(e) {
        if (!isResizing) return;
        const dw = e.clientX - startX, dh = e.clientY - startY;
        panel.style.width = Math.max(280, origW + dw) + 'px';
        panel.style.maxHeight = Math.max(200, origH + dh) + 'px';
    }
    function stopResize() {
        if (!isResizing) return;
        isResizing = false;
        panel.style.transition = '';
        savePanelGeometry(panel, true);   // size matters here  [M14 P10.5]
        document.removeEventListener('mousemove', onResize);
        document.removeEventListener('mouseup', stopResize);
    }
}


/* Event wiring that used to sit at the top level of script.js. Under ES
 * modules it cannot stay there: it touches elements owned by other
 * modules, and in an import cycle those bindings are still in their
 * temporal dead zone while this module is being evaluated. main.js calls
 * this once every module exists, in the original source order.
 *   [M14 P9.3] */
export function initWiring() {
    // Initialize drag & resize
    makeDraggable(remindersPanel, remindersPanelHeader);
    makeDraggable(notesPanel, notesPanelHeader);
    makeResizable(remindersPanel, document.getElementById('reminders-panel-resize'));
    makeResizable(notesPanel, document.getElementById('notes-panel-resize'));

    /* Restore where the user last left each panel. Done once at startup so a
       panel is already in place before it is first shown, rather than jumping
       after it appears.  [M14 P10.5] */
    restorePanelGeometry(remindersPanel);
    restorePanelGeometry(notesPanel);
}


/* ---------------------------------------------------------------------------
 * [M14 P9.3 moved from config.js]
 *
 * Settings persistence lives with the settings UI. It used to sit in
 * config.js, which meant a file of constants imported orbctl.js and this
 * module - a cycle, and cycles make top-level constant reads depend on
 * evaluation order. config.js is now a leaf, so any module can read a
 * constant while its own body runs.
 * ------------------------------------------------------------------------- */

export function loadSettings() {
    try {
        const s = localStorage.getItem(SETTINGS_KEY);
        if (s) {
            const parsed = JSON.parse(s);
            state.settings = { ...DEFAULT_SETTINGS, ...parsed };
        }
        if (toggleAutoActivity) toggleAutoActivity.checked = state.settings.autoOpenActivity;
        if (toggleAutoSearch) toggleAutoSearch.checked = state.settings.autoOpenSearchResults;
        if (toggleThinkingSounds) toggleThinkingSounds.checked = state.settings.thinkingSounds;
        // Load orb config from localStorage
        orbDashLoad();
    } catch (_) {}
}

export function saveSettings() {
    try {
        localStorage.setItem(SETTINGS_KEY, JSON.stringify(state.settings));
    } catch (_) {}
}

/* ---------------------------------------------------------------------------
 * Keep `inert` in step with what is actually visible.  [M14 P12.3]
 *
 * WHY THIS IS NOT JUST MIRRORING aria-hidden: five panels maintain aria-hidden
 * when they open and close, and three (activity, search results, settings)
 * only toggle a CSS class and never touch it. Mirroring the attribute would
 * silently leave those three tabbable, which is the exact bug being fixed.
 *
 * So visibility is measured rather than inferred from any one convention.
 * A panel counts as hidden when the browser would not paint it -- display
 * none, visibility hidden, zero opacity -- or when it has been translated
 * entirely outside the viewport, which is how the sliding panels hide. That
 * test is true regardless of whether a panel uses .open, .visible or
 * aria-hidden, so a future panel with a fourth convention is handled too.
 *
 * `inert` removes the whole subtree from the tab order, from the accessibility
 * tree, and from hit testing. It is the one-attribute version of what would
 * otherwise be tabindex bookkeeping on 36 controls.
 * ------------------------------------------------------------------------- */
/* Is this panel open, according to the app itself?
 *
 * Measured rather than assumed, because the conventions are not uniform:
 *   aria-hidden maintained : history, notes, reminders, orb dashboard, camera
 *   class only             : activity, search results, settings
 * Either signal means open. Checking both is what makes this work across all
 * eight without rewriting eight open/close functions.
 *
 * Deliberately NOT geometry-based. A transform-driven panel is off-screen for
 * the whole ~250ms of its opening animation, and openHistoryPanel() focuses
 * its search box immediately -- focus into an inert subtree is refused, so a
 * geometric test would have broken opening the panel by keyboard. State flips
 * on the first frame; position does not.
 */
function isPanelOpen(el) {
    if (el.classList.contains('open') || el.classList.contains('visible')) return true;
    if (el.getAttribute('aria-hidden') === 'false') return true;
    return false;
}

export function syncPanelInert() {
    const panels = document.querySelectorAll(INERT_PANEL_SELECTOR);
    let inertCount = 0;
    panels.forEach(panel => {
        const open = isPanelOpen(panel);
        if (!open) inertCount++;

        // One source of truth for both attributes. The three class-only panels
        // never set aria-hidden, so their contents were being announced while
        // invisible; that is fixed here rather than in three separate places.
        const ariaWanted = open ? 'false' : 'true';
        if (panel.getAttribute('aria-hidden') !== ariaWanted) {
            panel.setAttribute('aria-hidden', ariaWanted);
        }

        if (!open === panel.hasAttribute('inert')) return;
        if (!open) {
            // Never strand the caret inside a subtree that is about to leave
            // the accessibility tree: focus would land on nothing and the next
            // Tab would restart from the top of the document.
            if (panel.contains(document.activeElement)) {
                const composer = $('message-input');
                if (composer) composer.focus();
                else if (document.activeElement.blur) document.activeElement.blur();
            }
            panel.setAttribute('inert', '');
        } else {
            panel.removeAttribute('inert');
        }
    });
    return inertCount;
}

export function initPanelInert() {
    if (!('inert' in HTMLElement.prototype)) return;   // ancient browser: leave it alone

    // Coalesce bursts. A single panel open flips a class, an aria attribute and
    // an inline style, and every transitionend fires again on the way out.
    // setTimeout rather than requestAnimationFrame on purpose: rAF does not fire
    // in a headless verification run, and a11y wiring that cannot be verified
    // is a11y wiring that quietly rots.
    let pending = null;
    const schedule = () => {
        if (pending) return;
        pending = setTimeout(() => { pending = null; syncPanelInert(); }, 0);
    };

    const observer = new MutationObserver(schedule);
    document.querySelectorAll(INERT_PANEL_SELECTOR).forEach(panel => {
        observer.observe(panel, { attributes: true, attributeFilter: ['class', 'style', 'aria-hidden', 'hidden'] });
    });
    // No resize listener: openness is state, and state does not change
    // when the window does.
    syncPanelInert();
}
