/* ---------------------------------------------------------------------------
 * main.js
 *
 * Entry point: greeting, mode, global bindings, startup order.
 *
 * Extracted from the single 4000-line script.js by /data/p9_3b.py.
 * [M14 P9.3]
 * ------------------------------------------------------------------------- */

import { initWiring as initWiring_chat } from './chat.js';
import { initWiring as initWiring_notes } from './notes.js';
import { initWiring as initWiring_notifications } from './notifications.js';
import { initWiring as initWiring_panels } from './panels.js';
import { initWiring as initWiring_reminders } from './reminders.js';
import { checkHealth, preloadStarterAudio } from './api.js';
import { anyDialogOpen, autoResizeInput, charCount, chatMessages, messageInput, newChat, requestStopStreaming, sendBtn, sendMessage, setGreeting } from './chat.js';
import { initCommandPalette, registerCommand } from './commands.js';
import { $ } from './dom.js';
import { initPanelGeometry } from './geometry.js';
import { initHeaderMenu } from './headermenu.js';
import { initHistory, toggleHistoryPanel } from './history.js';
import { openNotesPanel } from './notes.js';
import { notificationVisibilityChanged } from './notifications.js';
import { initOrb, initOrbDashboard, orbDashBtn } from './orbctl.js';
import { activityClose, activityPanel, activityPollVisibilityChanged, activityToggle, initFloatPanelStacking, initPanelInert, loadSettings, monitorBtn, saveSettings, searchResultsClose, searchResultsToggle, searchResultsWidget, settingsBtn, settingsClose, settingsPanel, showToast, toggleAutoActivity, toggleAutoSearch, toggleThinkingSounds, updatePanelOverlay } from './panels.js';
import { openRemindersPanel } from './reminders.js';
import { initShortcutsOverlay } from './shortcuts.js';
import { state } from './state.js';
import { camBtn, initCameraPanel, startCamera, stopCamera } from './vision.js';
import { PreStarterPlayer, TTSPlayer, initPushToTalk, micBtn, playStartupBrief, ttsBtn } from './voice.js';
export const newChatBtn   = $('new-chat-btn');
export const modeSlider   = $('mode-slider');
export const btnJarvis    = $('btn-jarvis');

export function init() {
    if (!chatMessages || !messageInput) {
        console.error('[JARVIS] Required DOM elements (chat-messages, message-input) not found.');
        return;
    }
    loadSettings();
    state.ttsPlayer = new TTSPlayer();
    if (ttsBtn) ttsBtn.classList.add('tts-active');
    setGreeting();
    initOrb();
    initOrbDashboard();
    // Apply saved orb config now that orb instance exists
    if (state.orb) {
        state.orb.applyGlobals(state.orbGlobals);
    }
    initPushToTalk();
    preloadStarterAudio();
    state.preStarterPlayer = new PreStarterPlayer();
    checkHealth();
    // [M14 P7.1] Activity polling is per-turn now, started from sendMessage.
    // Starting it here as well would burn 15 ticks before the user has typed
    // anything and then stop, and nothing would ever restart it.
    playStartupBrief();
    bindEvents();
    initHistory();
    setMode(state.currentMode);
    autoResizeInput();
}

export function bindEvents() {
    if (sendBtn) sendBtn.addEventListener('click', () => { if (!state.isStreaming) sendMessage(); });
    if (messageInput) messageInput.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (!state.isStreaming) sendMessage(); }
    });
    if (messageInput) messageInput.addEventListener('input', () => {
        autoResizeInput();
        const len = messageInput.value.length;
        if (charCount) charCount.textContent = len > 100 ? `${len.toLocaleString()} / 32,000` : '';
    });
    if (camBtn) camBtn.addEventListener('click', () => {
        if (state.camStream) stopCamera();
        else startCamera();
    });
    initCameraPanel();
    initFloatPanelStacking();
    if (micBtn) micBtn.addEventListener('click', () => {
        showToast('Hold Ctrl+Shift to speak', 3000);
    });
    if (ttsBtn) ttsBtn.addEventListener('click', () => {
        if (state.ttsPlayer) state.ttsPlayer.enabled = !state.ttsPlayer.enabled;
        // [M14 P7.5] TTS can be switched on after load, and the starter
        // cache is only worth filling once it is. preloadStarterAudio is
        // idempotent, so flipping the toggle repeatedly is free.
        if (state.ttsPlayer && state.ttsPlayer.enabled) preloadStarterAudio();
        ttsBtn.classList.toggle('tts-active', state.ttsPlayer && state.ttsPlayer.enabled);
        if (state.ttsPlayer && !state.ttsPlayer.enabled) state.ttsPlayer.stop();
    });
    if (newChatBtn) newChatBtn.addEventListener('click', newChat);
    if (btnJarvis) btnJarvis.addEventListener('click', () => setMode('jarvis'));
    document.querySelectorAll('.chip').forEach(c => {
        c.addEventListener('click', () => { if (!state.isStreaming) sendMessage(c.dataset.msg); });
    });
    if (searchResultsToggle) {
        searchResultsToggle.addEventListener('click', () => {
            if (searchResultsWidget) { searchResultsWidget.classList.toggle('open'); updatePanelOverlay(); }
        });
    }
    if (searchResultsClose && searchResultsWidget) {
        searchResultsClose.addEventListener('click', () => { searchResultsWidget.classList.remove('open'); updatePanelOverlay(); });
    }
    if (activityToggle) {
        activityToggle.addEventListener('click', () => {
            if (activityPanel) { activityPanel.classList.toggle('open'); updatePanelOverlay(); }
        });
    }
    if (activityClose && activityPanel) {
        activityClose.addEventListener('click', () => { activityPanel.classList.remove('open'); updatePanelOverlay(); });
    }
    if (settingsBtn && settingsPanel) {
        settingsBtn.addEventListener('click', () => {
            settingsPanel.classList.toggle('open');
            updatePanelOverlay();
        });
    }
    
    if (monitorBtn) {
        monitorBtn.addEventListener('click', () => {
            window.open('/app/api-monitor.html', 'jarvisApiMonitor', 'width=1180,height=820,resizable=yes,scrollbars=yes');
        });
    }

    if (settingsClose && settingsPanel) {
        settingsClose.addEventListener('click', () => {
            settingsPanel.classList.remove('open');
            updatePanelOverlay();
        });
    }
    if (toggleAutoActivity) {
        toggleAutoActivity.addEventListener('change', () => {
            state.settings.autoOpenActivity = toggleAutoActivity.checked;
            saveSettings();
        });
    }
    if (toggleAutoSearch) {
        toggleAutoSearch.addEventListener('change', () => {
            state.settings.autoOpenSearchResults = toggleAutoSearch.checked;
            saveSettings();
        });
    }
    if (toggleThinkingSounds) {
        toggleThinkingSounds.addEventListener('change', () => {
            state.settings.thinkingSounds = toggleThinkingSounds.checked;
            saveSettings();
        });
    }

    /* ── Scroll-to-bottom FAB ── */
    const scrollFab = $('scroll-fab');
    if (scrollFab && chatMessages) {
        chatMessages.addEventListener('scroll', () => {
            const distFromBottom = chatMessages.scrollHeight - chatMessages.scrollTop - chatMessages.clientHeight;
            scrollFab.classList.toggle('visible', distFromBottom > 200);
            scrollFab.setAttribute('aria-hidden', distFromBottom <= 200 ? 'true' : 'false');
        });
        // Forced: pressing the button IS the request to go back down, even
        // though by definition the user is not near the bottom.  [M14 P10.7]
        scrollFab.addEventListener('click', () => {
            chatMessages.scrollTo({ top: chatMessages.scrollHeight, behavior: 'smooth' });
        });
    }

    /* ── Send button glow when text present ── */
    if (messageInput && sendBtn) {
        messageInput.addEventListener('input', () => {
            sendBtn.classList.toggle('has-text', messageInput.value.trim().length > 0);
        });
    }

}

export function setMode(mode) {
    state.currentMode = mode || 'jarvis';
    if (btnJarvis) btnJarvis.classList.add('active');
    if (modeSlider) modeSlider.classList.remove('center', 'right');
    if (activityToggle) activityToggle.style.display = '';
}


/* Event wiring that used to sit at the top level of script.js. Under ES
 * modules it cannot stay there: it touches elements owned by other
 * modules, and in an import cycle those bindings are still in their
 * temporal dead zone while this module is being evaluated. main.js calls
 * this once every module exists, in the original source order.
 *   [M14 P9.3] */
export function initWiring() {
    /* One visibility listener for the whole of script.js.  [M14 P7.6]
       P7.1 and P7.3 each wanted their own, and the relative order of separate
       listeners is undefined -- which matters, because both of these read
       `isStreaming`. Named calls in an explicit order instead. The orb keeps its
       own listener inside orb.js (P2.3): it owns its rAF loop and must stay usable
       when embedded without this file. Do not add a fourth listener here -- add a
       call to this one. */
    document.addEventListener('visibilitychange', () => {
        const hidden = document.hidden;
        activityPollVisibilityChanged(hidden);
        notificationVisibilityChanged(hidden);
    });
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape') return;
        if (anyDialogOpen()) return;
        if (requestStopStreaming()) e.preventDefault();
    });
}


/* Wiring runs in the order it ran when this was one file. main.js is the last
 * module evaluated, so every binding these touch already exists.
 *   [M14 P9.3] */
initWiring_panels();
initWiring_reminders();
initWiring_notes();
initWiring_notifications();
initWiring_chat();
initWiring();


/* ---------------------------------------------------------------------------
 * Command registry contents.  [M14 P10.2]
 *
 * Declared here, not in commands.js, so the registry stays a leaf of the
 * import graph. Every run() closes over the same function the corresponding
 * button calls, so there is no second implementation of "new chat" to drift
 * out of step, and when() hides a command that cannot currently do anything
 * instead of letting it fail loudly.
 * ------------------------------------------------------------------------- */
function registerAppCommands() {
    registerCommand({
        id: 'chat.new', title: 'New chat', group: 'Chat',
        keywords: ['reset', 'clear', 'start over'],
        run: () => newChat(),
    });
    registerCommand({
        id: 'chat.focus', title: 'Focus the message box', group: 'Chat',
        keywords: ['compose', 'type', 'input'],
        run: () => { if (messageInput) messageInput.focus(); },
    });
    registerCommand({
        id: 'chat.stop', title: 'Stop generating', group: 'Chat',
        keywords: ['abort', 'cancel', 'halt'], shortcut: 'Esc',
        when: () => state.isStreaming,
        run: () => requestStopStreaming(),
    });
    registerCommand({
        id: 'history.toggle', title: 'Toggle conversation history', group: 'Panels',
        keywords: ['past', 'sessions', 'previous'],
        run: () => toggleHistoryPanel(),
    });
    registerCommand({
        id: 'panels.activity', title: 'Toggle activity panel', group: 'Panels',
        keywords: ['steps', 'trace', 'flow'],
        when: () => !!activityPanel,
        run: () => { activityPanel.classList.toggle('open'); updatePanelOverlay(); },
    });
    registerCommand({
        id: 'panels.search', title: 'Toggle search results', group: 'Panels',
        keywords: ['sources', 'web', 'citations'],
        when: () => !!searchResultsWidget,
        run: () => { searchResultsWidget.classList.toggle('open'); updatePanelOverlay(); },
    });
    registerCommand({
        id: 'panels.settings', title: 'Toggle settings', group: 'Panels',
        keywords: ['preferences', 'options', 'config'],
        when: () => !!settingsPanel,
        run: () => { settingsPanel.classList.toggle('open'); updatePanelOverlay(); },
    });
    registerCommand({
        id: 'reminders.open', title: 'Open reminders', group: 'Panels',
        keywords: ['todo', 'due', 'alarm'],
        run: () => openRemindersPanel(),
    });
    registerCommand({
        id: 'notes.open', title: 'Open notes', group: 'Panels',
        keywords: ['notebook', 'todo lists'],
        run: () => openNotesPanel(),
    });
    registerCommand({
        id: 'camera.start', title: 'Start camera', group: 'Vision',
        keywords: ['webcam', 'video', 'see'],
        when: () => !state.camStream,
        run: () => startCamera(),
    });
    registerCommand({
        id: 'camera.stop', title: 'Stop camera', group: 'Vision',
        keywords: ['webcam', 'video', 'off'],
        when: () => !!state.camStream,
        run: () => stopCamera(),
    });
    registerCommand({
        id: 'orb.dashboard', title: 'Orb customization', group: 'Appearance',
        keywords: ['theme', 'colour', 'color', 'glow'],
        when: () => !!orbDashBtn,
        // The button owns the open/close behaviour; calling it keeps one path.
        run: () => orbDashBtn.click(),
    });

    /* ---------------------------------------------------------------------
     * Links to the other surfaces.  [M14 P11.6]
     *
     * J.A.R.V.I.S has five surfaces. The two admin dashboards, the API
     * monitor and the viewer all carry a visible nav row; the chat does not,
     * on purpose. This is the surface someone keeps open all day, and four
     * permanent header links you use once a week is how a header rots. The
     * palette is exactly the right home for "rare but real".
     *
     * All open in a new tab: navigating away mid-reply would abandon the
     * stream in progress.
     * ------------------------------------------------------------------- */
    const openSurface = (url) => window.open(url, '_blank', 'noopener');

    registerCommand({
        id: 'links.dashboard', title: 'Open Control Center', group: 'Links',
        keywords: ['dashboard', 'admin', 'system', 'health', 'skills', 'learner'],
        run: () => openSurface('/dashboard'),
    });
    registerCommand({
        id: 'links.watcher', title: 'Open Watcher', group: 'Links',
        keywords: ['world model', 'system state', 'monitor', 'daemon'],
        run: () => openSurface('/watcher'),
    });
    registerCommand({
        id: 'links.monitor', title: 'Open API Key Monitor', group: 'Links',
        keywords: ['keys', 'quota', 'usage', 'providers', 'limits'],
        run: () => openSurface('/jarvis/api-monitor.html'),
    });
    registerCommand({
        id: 'links.health', title: 'Open health check', group: 'Links',
        keywords: ['status', 'ping', 'alive', 'json'],
        run: () => openSurface('/health'),
    });
}

initPanelGeometry();
initHeaderMenu();
// After the panels exist and before the user can reach them with Tab.
// [M14 P12.3]
initPanelInert();
registerAppCommands();
initCommandPalette();
initShortcutsOverlay();

document.addEventListener('DOMContentLoaded', init);
