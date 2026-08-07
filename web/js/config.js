/* ---------------------------------------------------------------------------
 * config.js
 *
 * Constants and persisted settings. No behaviour, no DOM.
 *
 * Extracted from the single 4000-line script.js by /data/p9_3b.py.
 * [M14 P9.3]
 * ------------------------------------------------------------------------- */

import { state } from './state.js';
export const API = (typeof window !== 'undefined' && window.location.origin)
    ? window.location.origin
    : 'http://localhost:8000';
export const SETTINGS_KEY = 'jarvis_settings';
export const DEFAULT_SETTINGS = { autoOpenActivity: true, autoOpenSearchResults: true, thinkingSounds: true };

/* state.js deliberately imports nothing, so the defaults are seeded from here
 * rather than there. This runs at import time, before any module body that
 * reads state.settings, and keeps the default values defined in exactly one
 * place.  [M14 P9.3] */
Object.assign(state.settings, DEFAULT_SETTINGS);
// Thinking-cue phrases. These ride the SAME /tts voice cache as every other
// JARVIS line -- no separate pre-generated files, no separate generator script.
export const PRE_STARTER_PHRASES = ['One moment please.', 'Sure, one moment.', 'Got it, hold on.', 'On it right now.', 'Alright, give me a sec.', 'Right, one moment.', 'Okay, hold on.', 'One second please.', 'Give me a moment.', 'Just a moment please.'];

/* ══════════════════════════════════════════════════════════════════
   Orb Customization Dashboard
   ══════════════════════════════════════════════════════════════════ */

export const ORB_CONFIG_KEY = 'jarvis_orb_config';

// Default glow settings per state (CSS outer glow)
export const ORB_DEFAULT_GLOWS = {
    idle:      { color: '#7c6aef', size: 8,  pulse: 0   },
    listening: { color: '#4cc2e9', size: 30, pulse: 1.0 },
    thinking:  { color: '#8c5aff', size: 25, pulse: 0.8 },
    searching: { color: '#00c8b4', size: 30, pulse: 1.2 },
    working:   { color: '#f0a03c', size: 35, pulse: 1.0 },
    speaking:  { color: '#7c6aef', size: 20, pulse: 1.6 },
};

// Slider/control element IDs mapping
export const ORB_SLIDER_MAP = {
    speed:     { key: 'speedMul',   fmt: v => parseFloat(v).toFixed(1) },
    noise:     { key: 'noiseMul',   fmt: v => parseFloat(v).toFixed(2) },
    glow:      { key: 'glowMul',    fmt: v => parseFloat(v).toFixed(2) },
    wave:      { key: 'waveAmp',    fmt: v => parseFloat(v).toFixed(2) },
    orbit:     { key: 'orbitSpeed',  fmt: v => parseFloat(v).toFixed(1) },
    rot:       { key: 'rotSpeed',    fmt: v => parseFloat(v).toFixed(2) },
    hue:       { key: 'hue',         fmt: v => v + '°' },
};


export const CAM_BYPASS_TOKEN = 'TTCAMTOKENTT';
export const CAMERA_QUERY_PATTERNS = [
    /what\s+(can|do)\s+you\s+see/i,
    /can\s+you\s+see/i,
    /describe\s+(what\s+you\s+see|this|the\s+image)/i,
    /what('s|s)\sss+in\sss+(this\sss+)?(picture|image)/i,
    /what\s+do\s+i\s+look\s+like/i,
    /what\s+(am\s+i\s+)?holding/i,
    /show\s+me\s+what\s+you\s+see/i,
];

/* ── Status Badge: show current orb state instead of "Online" ── */
export const ORB_STATE_LABELS = {
    idle:      'Online',
    listening: 'Listening',
    thinking:  'Thinking',
    searching: 'Searching',
    working:   'Working',
    speaking:  'Speaking',
};

export const ACTIVITY_STEPS = {
    voice_input:           { step: 0, label: 'Voice captured' },
    query_detected:        { step: 1, label: 'Query detected' },
    // M13: what JARVIS understood, shown every turn. This is the mitigation for
    // a confidently-wrong resolution -- you see the misunderstanding itself,
    // not just the wrong outcome.
    understood:            { step: 2, label: 'Understood' },
    decision:              { step: 2, label: 'Route decided' },
    intent_classified:     { step: 0, label: 'Task intent' },
    routing:               { step: 3, label: 'Route selected' },
    context_retrieved:     { step: 0, label: 'Context loaded' },
    extracting_query:      { step: 0, label: 'Extracting query' },
    searching_web:         { step: 0, label: 'Searching web' },
    search_completed:      { step: 0, label: 'Search done' },
    vision_analyzing:      { step: 0, label: 'Analyzing image' },
    tasks_executing:       { step: 0, label: 'Running tasks' },
    tasks_completed:       { step: 0, label: 'Tasks done' },
    actions_emitted:       { step: 0, label: 'Actions sent' },
    background_dispatched: { step: 0, label: 'Background tasks' },
    streaming_started:     { step: 4, label: 'Generating response' },
    first_chunk:           { step: 5, label: 'Response ready' },
    tts_cache_hit:         { step: 0, label: 'Voice: Cache hit' },
    tts_cache_miss_saved:  { step: 0, label: 'Voice: Downloaded & saved' },
    agent_started:         { step: 3, label: 'Agent started' },
    execution_started:     { step: 0, label: 'Execution started' },
    cache_miss:            { step: 0, label: 'Command cache' },
    cache_hit:             { step: 0, label: 'Command cache' },
    cache_replay:          { step: 0, label: 'Cached replay' },
    verification_queued:   { step: 0, label: 'Verification queued' },
    verification_result:   { step: 0, label: 'Verification result' },
    // M13 §3.4: the turn now waits for the verdict before it speaks.
    verifying:             { step: 0, label: 'Checking it worked' },
    verdict:               { step: 0, label: 'Verdict' },
    retrying:              { step: 0, label: 'Retrying' },
    // M13 §3.3: an action turn that called no tool at all.
    no_op_rejected:        { step: 0, label: 'Nothing was executed' },
    cache_status:          { step: 0, label: 'Cache update' },
    execution_completed:   { step: 0, label: 'Execution completed' },
    confirmation_granted:  { step: 0, label: 'Confirmation accepted' },
    frontend_ack:          { step: 0, label: 'Browser action' },
    tool_call:             { step: 0, label: 'Tool call' },
    tool_result:           { step: 0, label: 'Tool result' },
    awaiting_confirmation: { step: 0, label: 'Needs confirmation' },
    agent_done:            { step: 5, label: 'Agent done' },
    llm_provider:          { step: 0, label: 'Answered by' },
    provider_failover:     { step: 0, label: 'Failover' },
    provider_race:         { step: 0, label: 'Race winner' },
};

export const ACTIVITY_POLL_MS = 2000;
// 15 ticks = 30s with no new rows. The slowest settle profile finishes well
// inside that, so this stops shortly after the last verdict instead of never.
export const ACTIVITY_POLL_MAX_IDLE_TICKS = 15;

export const AVATAR_ICON_USER = '<svg class="msg-avatar-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>';
export const AVATAR_ICON_ASSISTANT = '<svg class="msg-avatar-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="2"/><path d="M12 7v4"/><circle cx="9" cy="16" r="1" fill="currentColor"/><circle cx="15" cy="16" r="1" fill="currentColor"/></svg>';

/* =====================================================================
   M14 P3: streaming resilience
   A dead stream used to hang behind a single 300s abort, and any failure threw
   away every character that had already arrived. These helpers add an idle
   watchdog, a real Stop control, and a recovery path that keeps partial text.
   ===================================================================== */
export const STREAM_IDLE_TIMEOUT_MS = 45000;
export const STREAM_TOTAL_TIMEOUT_MS = 600000;
export const STREAM_INCOMPLETE_LABEL = {
    lost: '\u2301 Response incomplete \u2014 connection lost.',
    stopped: '\u25A0 Stopped.'
};

/* ─────────────────────────────────────────
   Generic Panel Drag & Resize
   ───────────────────────────────────────── */
/* ---------------------------------------------------------------------------
 * [M14 P8.3] Click-to-front for the floating panels.
 *
 * P8.1 collapsed .cam-panel and .jarvis-panel onto one stacking token,
 * --z-float, which is correct: they are peers, and a fixed winner would mean
 * one of them could never be read while the other is open. Peers at the same
 * layer are resolved by DOM order, which the user cannot see or change, so the
 * panel they just touched is promoted one step above the shared layer and the
 * previous winner is demoted back onto it.
 *
 * The listener is on the capture phase of mousedown so it still fires when the
 * panel's own drag handler stops propagation, and pointerdown is not used
 * because the drag handlers this sits next to are mousedown-based.
 * ------------------------------------------------------------------------- */
export const FLOAT_PANEL_SELECTOR = '.cam-panel, .jarvis-panel';

/* Every panel that hides itself WITHOUT display:none.  [M14 P12.3]

   These slide away with a transform, or fade with visibility/opacity. All of
   those leave the element rendered, which means its buttons stay in the tab
   order. Measured before writing this: with every panel closed there were 36
   focusable controls sitting inside them, and focusing one was accepted by the
   browser. A keyboard user tabbing out of the header lands inside an invisible
   panel with no way to tell where they are.

   initPanelInert() in panels.js keeps the `inert` attribute in step with what
   is actually on screen. Add a panel here when you add one to index.html. */
export const INERT_PANEL_SELECTOR = [
    '.history-panel',
    '.activity-panel',
    '.search-results-widget',
    '.settings-panel',
    '.orb-dashboard',
    '.cam-panel',
    '.jarvis-panel',
].join(', ');
export const NOTIF_RETRY_MAX = 30000;

/* ===================== Conversation History ===================== */
export const HISTORY_SEARCH_DEBOUNCE_MS = 280;

/* --- URL routing -------------------------------------------------------
 * The URL is the single source of truth for which conversation is open:
 *   /jarvis/            -> a fresh chat (no id yet)
 *   /jarvis/c/<id>      -> that conversation
 * A new chat gets its id only once the first message comes back from the
 * server, at which point the URL is *replaced* (not pushed) so Back does not
 * land on an empty draft. Same behaviour as ChatGPT.
 * The app is also mounted at /app, so the base is derived, not hardcoded. */
export const CHAT_BASE_PATH  = location.pathname.startsWith('/app') ? '/app/' : '/jarvis/';
export const CHAT_URL_PREFIX = CHAT_BASE_PATH + 'c/';

export const HISTORY_GROUP_ORDER = ['Today', 'Yesterday', 'Previous 7 Days', 'Previous 30 Days', 'Older'];

/* How long a panel the AGENT opened stays up before closing itself. Only
 * agent-opened panels auto-close at all; one the user opened stays until the
 * user closes it. Long enough to read a list of reminders, and cancelled
 * outright the moment the user touches the panel.  [M14 P10.6] */
export const PANEL_AUTO_CLOSE_MS = 60000;

/* At most this many toasts are on screen; the rest wait behind a counter. */
export const TOAST_MAX_VISIBLE = 3;
