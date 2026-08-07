/* ---------------------------------------------------------------------------
 * history.js
 *
 * Conversation history panel, deep links, rename and delete.
 *
 * Extracted from the single 4000-line script.js by /data/p9_3b.py.
 * [M14 P9.3]
 * ------------------------------------------------------------------------- */

import { EVENTS, emit } from './bus.js';
import { addMessage, attachCopyButton, chatMessages, createWelcome, messageInput, newChat, renderMarkdownInto, scrollToBottom } from './chat.js';
import { API, CHAT_BASE_PATH, CHAT_URL_PREFIX, HISTORY_GROUP_ORDER, HISTORY_SEARCH_DEBOUNCE_MS } from './config.js';
import { $ } from './dom.js';
import { updateOrbOcclusion } from './orbctl.js';
import { activityList, activityPanel, activityToggle, searchResultsToggle, searchResultsWidget, showToast, updatePanelOverlay } from './panels.js';
import { state } from './state.js';
import { pttStopRecording, stopStartupBrief } from './voice.js';
export function sessionIdFromUrl() {
    if (!location.pathname.startsWith(CHAT_URL_PREFIX)) return null;
    const raw = location.pathname.slice(CHAT_URL_PREFIX.length).replace(/\/+$/, '');
    if (!raw) return null;
    try { return decodeURIComponent(raw); } catch (_) { return raw; }
}

/** mode: 'replace' (default) | 'push' | 'none'. */
export function syncUrl(id, mode) {
    if (mode === 'none') return;
    const path = id ? CHAT_URL_PREFIX + encodeURIComponent(id) : CHAT_BASE_PATH;
    if (location.pathname === path) return;
    const entry = { sessionId: id || null };
    try {
        if (mode === 'push') history.pushState(entry, '', path);
        else history.replaceState(entry, '', path);
    } catch (_) { /* history API unavailable -- the app still works */ }
}

export const historyPanel        = $('history-panel');
export const historyToggle       = $('history-toggle');
export const historyClose        = $('history-close');
export const historyOverlay      = $('history-overlay');
export const historyList         = $('history-list');
export const historyNewBtn       = $('history-new-btn');
export const historySearchInput  = $('history-search-input');
export const historySearchClear  = $('history-search-clear');
export const historyLoading      = $('history-loading');
export const historyEmpty        = $('history-empty');
export const historyNoResults    = $('history-no-results');
export const historyError        = $('history-error');
export const historyRetry        = $('history-retry');
export const historyLoadMore     = $('history-load-more');
export const historyStatus       = $('history-status');
export const historyDialogBackdrop = $('history-dialog-backdrop');
export const historyRenameDialog = $('history-rename-dialog');
export const historyRenameInput  = $('history-rename-input');
export const historyRenameCancel = $('history-rename-cancel');
export const historyRenameSave   = $('history-rename-save');
export const historyDeleteDialog = $('history-delete-dialog');
export const historyDeleteName   = $('history-delete-name');
export const historyDeleteCancel = $('history-delete-cancel');
export const historyDeleteConfirm = $('history-delete-confirm');

export const historyState = {
    items: [],
    cursor: null,
    query: '',
    loading: false,
    switching: false,
    openMenuId: null,
    dialogSessionId: null,
    // Bumped on every load/switch so a slow response can never render over a
    // newer one.
    requestToken: 0,
    searchTimer: null,
    lastFocus: null,
};

/* The browser tab title.  [M14 P12.5]
 *
 * It was the static string "J.A.R.V.I.S", so six open conversations were six
 * identical tabs and browser history was a wall of the same entry. The
 * conversation name goes first because tab strips truncate from the right --
 * putting the product name first would make every tab read "J.A.R.V.I...".
 *
 * Falls back to the bare product name for a draft that has no title yet, which
 * is honest: an untitled conversation has nothing to show. */
const BASE_TITLE = 'J.A.R.V.I.S';

export function setDocumentTitle(title) {
    const clean = (title || '').trim();
    document.title = clean ? `${clean} \u00b7 ${BASE_TITLE}` : BASE_TITLE;
}

function titleForSession(id) {
    if (!id) return '';
    const item = historyState.items.find(it => it.session_id === id);
    return item ? (item.title || '') : '';
}

/** Single place where the active session id changes, so the URL and the sidebar
 *  highlight can never drift from the streaming session.
 *  urlMode: 'replace' (default -- a draft becoming a real conversation),
 *           'push' (user navigated to another conversation),
 *           'none' (we got here *from* the URL, e.g. Back/Forward). */
export function setActiveSession(id, urlMode = 'replace') {
    const changed = state.sessionId !== id;
    state.sessionId = id;
    syncUrl(id, urlMode);
    if (changed) emit(EVENTS.SESSION_CHANGE, { sessionId: id });
    if (changed) setDocumentTitle(titleForSession(id));
    if (changed && id) {
        markActiveHistoryItem();
        // A brand new conversation has no summary yet; refresh so it appears.
        if (!historyState.items.some(it => it.session_id === id)) {
            loadHistory({ silent: true });
        }
    }
}

export function announceHistory(message) {
    if (historyStatus) historyStatus.textContent = message || '';
}

export function isHistoryOpen() {
    return !!(historyPanel && historyPanel.classList.contains('open'));
}

export function openHistoryPanel() {
    if (!historyPanel) return;
    historyState.lastFocus = document.activeElement;
    historyPanel.classList.add('open');
    historyPanel.setAttribute('aria-hidden', 'false');
    if (historyToggle) historyToggle.setAttribute('aria-expanded', 'true');
    if (historyOverlay) {
        historyOverlay.hidden = false;
        requestAnimationFrame(() => historyOverlay.classList.add('open'));
    }
    loadHistory();
    if (historySearchInput) historySearchInput.focus();
}

export function closeHistoryPanel() {
    if (!historyPanel) return;
    closeHistoryMenu();
    historyPanel.classList.remove('open');
    historyPanel.setAttribute('aria-hidden', 'true');
    if (historyToggle) historyToggle.setAttribute('aria-expanded', 'false');
    if (historyOverlay) {
        historyOverlay.classList.remove('open');
        setTimeout(() => { if (!isHistoryOpen()) historyOverlay.hidden = true; }, 300);
    }
    if (historyState.lastFocus && typeof historyState.lastFocus.focus === 'function') {
        historyState.lastFocus.focus();
    }
}

export function toggleHistoryPanel() {
    if (isHistoryOpen()) closeHistoryPanel(); else openHistoryPanel();
}

export function setHistoryStateView({ loading, empty, noResults, error }) {
    if (historyLoading)   historyLoading.hidden   = !loading;
    if (historyEmpty)     historyEmpty.hidden     = !empty;
    if (historyNoResults) historyNoResults.hidden = !noResults;
    if (historyError)     historyError.hidden     = !error;
}

export async function loadHistory({ append = false, silent = false } = {}) {
    if (!historyList) return;
    const token = ++historyState.requestToken;
    historyState.loading = true;
    if (!append && !silent) {
        historyList.innerHTML = '';
        setHistoryStateView({ loading: true });
        if (historyLoadMore) historyLoadMore.hidden = true;
    }

    const params = new URLSearchParams();
    if (historyState.query) params.set('query', historyState.query);
    if (append && historyState.cursor) params.set('cursor', historyState.cursor);

    try {
        const res = await fetch(`${API}/chat/history?${params.toString()}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (token !== historyState.requestToken) return;  // a newer load won
        const incoming = Array.isArray(data.conversations) ? data.conversations : [];
        historyState.items = append ? historyState.items.concat(incoming) : incoming;
        historyState.cursor = data.next_cursor || null;
        renderHistory();
    } catch (err) {
        if (token !== historyState.requestToken) return;
        console.error('[JARVIS] History load failed:', err);
        if (!append) {
            historyList.innerHTML = '';
            setHistoryStateView({ error: true });
        }
        announceHistory('Could not load conversation history.');
    } finally {
        historyState.loading = false;
    }
}

/** Groups by calendar day, not by elapsed hours -- 8pm yesterday viewed at 9am
 *  is "Yesterday", even though it is only 13 hours ago. */
export function historyGroupFor(isoString) {
    const then = new Date(isoString);
    if (isNaN(then.getTime())) return 'Older';
    const startOfToday = new Date();
    startOfToday.setHours(0, 0, 0, 0);
    const startOfThen = new Date(then);
    startOfThen.setHours(0, 0, 0, 0);
    // Rounded so a DST shift cannot push a day into the wrong bucket.
    const days = Math.round((startOfToday.getTime() - startOfThen.getTime()) / 86400000);
    if (days <= 0) return 'Today';
    if (days === 1) return 'Yesterday';
    if (days < 7) return 'Previous 7 Days';
    if (days < 30) return 'Previous 30 Days';
    return 'Older';
}

export function historyRelativeTime(isoString) {
    const then = new Date(isoString);
    if (isNaN(then.getTime())) return '';
    const secs = Math.floor((Date.now() - then.getTime()) / 1000);
    if (secs < 60) return 'just now';
    if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
    if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
    const days = Math.floor(secs / 86400);
    if (days < 7) return `${days}d ago`;
    return then.toLocaleDateString();
}

export function renderHistory() {
    if (!historyList) return;
    closeHistoryMenu();
    historyList.innerHTML = '';

    if (!historyState.items.length) {
        setHistoryStateView(historyState.query ? { noResults: true } : { empty: true });
        if (historyLoadMore) historyLoadMore.hidden = true;
        return;
    }
    setHistoryStateView({});

    const groups = new Map();
    for (const item of historyState.items) {
        const label = historyGroupFor(item.updated_at);
        if (!groups.has(label)) groups.set(label, []);
        groups.get(label).push(item);
    }

    for (const label of HISTORY_GROUP_ORDER) {
        const bucket = groups.get(label);
        if (!bucket || !bucket.length) continue;
        const heading = document.createElement('div');
        heading.className = 'history-group-label';
        heading.textContent = label;
        historyList.appendChild(heading);
        bucket.forEach(item => historyList.appendChild(buildHistoryItem(item)));
    }

    if (historyLoadMore) historyLoadMore.hidden = !historyState.cursor;
    markActiveHistoryItem();
}

/** All user-controlled text goes in via textContent -- never innerHTML. */
export function buildHistoryItem(item) {
    const row = document.createElement('div');
    row.className = 'history-item';
    row.dataset.sessionId = item.session_id;

    const main = document.createElement('button');
    main.type = 'button';
    main.className = 'history-item-main';
    main.style.cssText = 'background:none;border:none;color:inherit;font:inherit;text-align:left;cursor:pointer;padding:0;';

    const title = document.createElement('span');
    title.className = 'history-item-title';
    title.textContent = item.title || 'New conversation';

    const meta = document.createElement('span');
    meta.className = 'history-item-meta';
    const count = item.message_count || 0;
    meta.textContent = `${historyRelativeTime(item.updated_at)} · ${count} message${count === 1 ? '' : 's'}`;

    main.appendChild(title);
    main.appendChild(meta);
    main.addEventListener('click', () => selectConversation(item.session_id));

    const menuBtn = document.createElement('button');
    menuBtn.type = 'button';
    menuBtn.className = 'history-item-menu-btn';
    menuBtn.title = 'Conversation options';
    menuBtn.setAttribute('aria-label', `Options for ${item.title || 'conversation'}`);
    menuBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="12" cy="19" r="1.6"/></svg>';
    menuBtn.addEventListener('click', e => {
        e.stopPropagation();
        toggleHistoryMenu(item, row);
    });

    row.appendChild(main);
    row.appendChild(menuBtn);
    return row;
}

export function closeHistoryMenu() {
    historyState.openMenuId = null;
    document.querySelectorAll('.history-item-menu').forEach(m => m.remove());
    document.querySelectorAll('.history-item.menu-open').forEach(r => r.classList.remove('menu-open'));
}

export function toggleHistoryMenu(item, row) {
    const alreadyOpen = historyState.openMenuId === item.session_id;
    closeHistoryMenu();
    if (alreadyOpen) return;

    historyState.openMenuId = item.session_id;
    row.classList.add('menu-open');

    const menu = document.createElement('div');
    menu.className = 'history-item-menu';
    menu.setAttribute('role', 'menu');

    const rename = document.createElement('button');
    rename.type = 'button';
    rename.className = 'history-menu-item';
    rename.setAttribute('role', 'menuitem');
    rename.textContent = 'Rename';
    rename.addEventListener('click', e => { e.stopPropagation(); closeHistoryMenu(); openRenameDialog(item); });

    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'history-menu-item danger';
    del.setAttribute('role', 'menuitem');
    del.textContent = 'Delete';
    del.addEventListener('click', e => { e.stopPropagation(); closeHistoryMenu(); openDeleteDialog(item); });

    menu.appendChild(rename);
    menu.appendChild(del);
    row.appendChild(menu);

    // Flip the menu above the row when it would be clipped by the scroll area.
    if (historyList) {
        const menuBox = menu.getBoundingClientRect();
        const listBox = historyList.getBoundingClientRect();
        if (menuBox.bottom > listBox.bottom) menu.classList.add('up');
    }
    rename.focus();
}

export function markActiveHistoryItem() {
    // The summaries usually land AFTER the session was selected, so this is
    // where a title first becomes knowable on a cold load.  [M14 P12.5]
    setDocumentTitle(titleForSession(state.sessionId));
    if (!historyList) return;
    historyList.querySelectorAll('.history-item').forEach(row => {
        row.classList.toggle('active', row.dataset.sessionId === state.sessionId);
    });
}

/** opts.urlMode -- 'push' (default) for a user click, 'none' when we are
 *  reacting to the URL itself (initial load, Back/Forward).
 *  Resolves true when the conversation was opened. */
export async function selectConversation(id, opts = {}) {
    const urlMode = opts.urlMode || 'push';
    if (!id || historyState.switching) return false;
    if (id === state.sessionId) { closeHistoryPanelOnMobile(); return true; }
    if (state.isStreaming) {
        showToast('Jarvis is still replying — wait for it to finish.');
        return false;
    }

    const token = ++historyState.requestToken;
    historyState.switching = true;
    announceHistory('Opening conversation…');

    try {
        const res = await fetch(`${API}/chat/history/${encodeURIComponent(id)}`);
        if (res.status === 404) {
            showToast('That conversation no longer exists.');
            historyState.items = historyState.items.filter(it => it.session_id !== id);
            renderHistory();
            // A dead id in the address bar would 404 again on every refresh.
            if (urlMode === 'none') syncUrl(null, 'replace');
            return false;
        }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (token !== historyState.requestToken) return false;  // user switched again

        if (state.isRecording) pttStopRecording();
        if (state.ttsPlayer) state.ttsPlayer.stop();
        stopStartupBrief();

        chatMessages.innerHTML = '';
        // Assistant transcripts are markdown and must render as such, or
        // reopening a conversation shows raw syntax in exactly the place a
        // user goes to re-read something. User messages stay plain text: a
        // user who typed an asterisk meant an asterisk. [M14 P6]
        (data.messages || []).forEach(m => {
            const isAssistant = m.role === 'assistant';
            const content = addMessage(isAssistant ? 'assistant' : 'user', isAssistant ? '' : (m.content || ''));
            if (isAssistant) {
                const host = document.createElement('span');
                host.className = 'msg-stream-text';
                content.textContent = '';
                content.appendChild(host);
                renderMarkdownInto(content, m.content || '', false, null);
                // A reopened transcript is exactly where someone goes to copy
                // something out of an old answer.  [M14 P10.7]
                attachCopyButton(content, m.content || '');
            }
        });
        if (!(data.messages || []).length) chatMessages.appendChild(createWelcome());

        // Activity/search panels belong to the current turn, not to a reopened
        // transcript -- reset rather than show stale telemetry.
        resetTurnPanels();
        setActiveSession(id, urlMode);
        markActiveHistoryItem();
        // Forced: opening a conversation is the user's own action, and the
        // newest message is the one they came for.  [M14 P10.7]
        scrollToBottom(true);
        announceHistory(`Opened ${data.title || 'conversation'}.`);
        closeHistoryPanelOnMobile();
        if (messageInput) messageInput.focus();
        return true;
    } catch (err) {
        console.error('[JARVIS] Could not open conversation:', err);
        showToast('Could not open that conversation.');
        return false;
    } finally {
        // Reset unconditionally: only one switch runs at a time (guarded at
        // entry), and a concurrent search bumping requestToken must not leave
        // this stuck true and block every future switch.
        historyState.switching = false;
    }
}

/** Clears per-turn UI that must not carry across a conversation switch. */
export function resetTurnPanels() {
    if (searchResultsWidget) searchResultsWidget.classList.remove('open');
    if (searchResultsToggle) searchResultsToggle.style.display = 'none';
    if (activityPanel) activityPanel.classList.remove('open');
    if (activityList) {
        activityList.innerHTML = '<div class="activity-empty" id="activity-empty">Send a message to see the flow here.</div>';
    }
    if (activityToggle) activityToggle.style.display = 'none';
    updatePanelOverlay();
}

export function closeHistoryPanelOnMobile() {
    if (window.matchMedia('(max-width: 768px)').matches) closeHistoryPanel();
}

/* ── Rename ── */
export function openRenameDialog(item) {
    if (!historyRenameDialog || !historyDialogBackdrop) return;
    historyState.dialogSessionId = item.session_id;
    historyState.lastFocus = document.activeElement;
    historyRenameInput.value = item.title || '';
    historyDialogBackdrop.hidden = false;
    updateOrbOcclusion();
    historyRenameDialog.hidden = false;
    if (historyDeleteDialog) historyDeleteDialog.hidden = true;
    historyRenameInput.focus();
    historyRenameInput.select();
}

export function closeHistoryDialogs() {
    if (historyDialogBackdrop) historyDialogBackdrop.hidden = true;
    updateOrbOcclusion();
    if (historyRenameDialog) historyRenameDialog.hidden = true;
    if (historyDeleteDialog) historyDeleteDialog.hidden = true;
    historyState.dialogSessionId = null;
    if (historyState.lastFocus && typeof historyState.lastFocus.focus === 'function') {
        historyState.lastFocus.focus();
    }
}

export async function submitRename() {
    const id = historyState.dialogSessionId;
    const nextTitle = (historyRenameInput.value || '').trim();
    if (!id || !nextTitle) return;

    const item = historyState.items.find(it => it.session_id === id);
    const previousTitle = item ? item.title : '';
    if (item) { item.title = nextTitle; renderHistory(); }   // optimistic
    closeHistoryDialogs();

    try {
        const res = await fetch(`${API}/chat/history/${encodeURIComponent(id)}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title: nextTitle }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const summary = await res.json();
        const target = historyState.items.find(it => it.session_id === id);
        if (target && summary && summary.title) target.title = summary.title;
        renderHistory();
        announceHistory('Conversation renamed.');
    } catch (err) {
        console.error('[JARVIS] Rename failed:', err);
        if (item) { item.title = previousTitle; renderHistory(); }   // rollback
        showToast('Could not rename that conversation.');
    }
}

/* ── Delete (permanent) ── */
export function openDeleteDialog(item) {
    if (!historyDeleteDialog || !historyDialogBackdrop) return;
    historyState.dialogSessionId = item.session_id;
    historyState.lastFocus = document.activeElement;
    if (historyDeleteName) historyDeleteName.textContent = item.title || 'This conversation';
    historyDialogBackdrop.hidden = false;
    updateOrbOcclusion();
    historyDeleteDialog.hidden = false;
    if (historyRenameDialog) historyRenameDialog.hidden = true;
    if (historyDeleteConfirm) historyDeleteConfirm.focus();
}

export async function submitDelete() {
    const id = historyState.dialogSessionId;
    if (!id) return;
    if (id === state.sessionId && state.isStreaming) {
        showToast('Jarvis is still replying in this conversation.');
        return;
    }
    if (historyDeleteConfirm) historyDeleteConfirm.disabled = true;

    try {
        const res = await fetch(`${API}/chat/history/${encodeURIComponent(id)}`, { method: 'DELETE' });
        if (!res.ok && res.status !== 404) throw new Error(`HTTP ${res.status}`);
        historyState.items = historyState.items.filter(it => it.session_id !== id);
        const wasActive = id === state.sessionId;
        closeHistoryDialogs();
        renderHistory();
        if (wasActive) newChat();
        announceHistory('Conversation deleted.');
    } catch (err) {
        console.error('[JARVIS] Delete failed:', err);
        showToast('Could not delete that conversation.');
    } finally {
        if (historyDeleteConfirm) historyDeleteConfirm.disabled = false;
    }
}

/** Open whatever conversation the address bar points at. A bare /jarvis/ means
 *  a fresh chat -- the URL, not localStorage, decides what is open, so two tabs
 *  can hold two different conversations. */
export function openSessionFromUrl() {
    const id = sessionIdFromUrl();
    if (!id) return;
    selectConversation(id, { urlMode: 'none' });
}

/** Back/Forward. The URL has already changed by the time this fires, so the
 *  handler only brings the app in line with it -- never writes history back. */
export function onHistoryPopState() {
    const id = sessionIdFromUrl();
    if (id === state.sessionId) return;
    if (state.isStreaming) {
        // Cannot swap conversations mid-reply; put the address bar back.
        showToast('Jarvis is still replying — wait for it to finish.');
        syncUrl(state.sessionId, 'replace');
        return;
    }
    if (!id) { newChat({ urlMode: 'none' }); return; }
    selectConversation(id, { urlMode: 'none' });
}

export function initHistory() {
    if (!historyPanel) return;

    if (historyToggle) historyToggle.addEventListener('click', toggleHistoryPanel);
    if (historyClose) historyClose.addEventListener('click', closeHistoryPanel);
    if (historyOverlay) historyOverlay.addEventListener('click', closeHistoryPanel);
    if (historyRetry) historyRetry.addEventListener('click', () => loadHistory());
    if (historyLoadMore) historyLoadMore.addEventListener('click', () => loadHistory({ append: true }));
    if (historyNewBtn) historyNewBtn.addEventListener('click', () => { newChat(); closeHistoryPanelOnMobile(); });

    if (historySearchInput) {
        historySearchInput.addEventListener('input', () => {
            const value = historySearchInput.value;
            if (historySearchClear) historySearchClear.hidden = !value;
            clearTimeout(historyState.searchTimer);
            historyState.searchTimer = setTimeout(() => {
                historyState.query = value.trim();
                historyState.cursor = null;
                loadHistory();
            }, HISTORY_SEARCH_DEBOUNCE_MS);
        });
    }
    if (historySearchClear) {
        historySearchClear.addEventListener('click', () => {
            historySearchInput.value = '';
            historySearchClear.hidden = true;
            historyState.query = '';
            historyState.cursor = null;
            loadHistory();
            historySearchInput.focus();
        });
    }

    if (historyRenameCancel) historyRenameCancel.addEventListener('click', closeHistoryDialogs);
    if (historyRenameSave) historyRenameSave.addEventListener('click', submitRename);
    if (historyRenameInput) {
        historyRenameInput.addEventListener('keydown', e => {
            if (e.key === 'Enter') { e.preventDefault(); submitRename(); }
        });
    }
    if (historyDeleteCancel) historyDeleteCancel.addEventListener('click', closeHistoryDialogs);
    if (historyDeleteConfirm) historyDeleteConfirm.addEventListener('click', submitDelete);
    if (historyDialogBackdrop) {
        historyDialogBackdrop.addEventListener('click', e => {
            if (e.target === historyDialogBackdrop) closeHistoryDialogs();
        });
    }

    document.addEventListener('click', e => {
        if (historyState.openMenuId && !e.target.closest('.history-item')) closeHistoryMenu();
    });
    document.addEventListener('keydown', e => {
        if (e.key !== 'Escape') return;
        if (historyDialogBackdrop && !historyDialogBackdrop.hidden) { closeHistoryDialogs(); return; }
        if (historyState.openMenuId) { closeHistoryMenu(); return; }
        if (isHistoryOpen()) closeHistoryPanel();
    });

    window.addEventListener('popstate', onHistoryPopState);
    openSessionFromUrl();
}
