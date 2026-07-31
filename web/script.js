
const API = (typeof window !== 'undefined' && window.location.origin)
    ? window.location.origin
    : 'http://localhost:8000';

let sessionId = null;
let currentMode = 'jarvis';
let isStreaming = false;
let camStream = null;

/* ── Push-to-Talk (Ctrl+Shift) state ── */
let isRecording = false;
let currentTranscript = '';
let finalReceived = false;
let pttSafetyTimer = null;
let ctrlHeld = false;
let shiftHeld = false;
let pttSendDone = false;
let pttVoiceSource = null;  // 'web-speech' | 'whisper' | null
let orb = null;
let recognition = null;
let ttsPlayer = null;
const SETTINGS_KEY = 'jarvis_settings';
const DEFAULT_SETTINGS = { autoOpenActivity: true, autoOpenSearchResults: true, thinkingSounds: true };
// Thinking-cue phrases. These ride the SAME /tts voice cache as every other
// JARVIS line -- no separate pre-generated files, no separate generator script.
const PRE_STARTER_PHRASES = ['One moment please.', 'Sure, one moment.', 'Got it, hold on.', 'On it right now.', 'Alright, give me a sec.', 'Right, one moment.', 'Okay, hold on.', 'One second please.', 'Give me a moment.', 'Just a moment please.'];
let PRE_STARTER_CACHE = {};
let settings = { ...DEFAULT_SETTINGS };
const backgroundActivitySeen = new Set();
const backgroundActivityStartedAt = Date.now() / 1000;
const $ = id => document.getElementById(id);
const chatMessages = $('chat-messages');
const messageInput = $('message-input');
const sendBtn      = $('send-btn');
const micBtn       = $('mic-btn');
const ttsBtn       = $('tts-btn');
const newChatBtn   = $('new-chat-btn');
const charCount    = $('char-count');
const welcomeTitle = $('welcome-title');
const modeSlider   = $('mode-slider');
const btnJarvis    = $('btn-jarvis');
const statusDot    = document.querySelector('.status-dot');
const statusText   = document.querySelector('.status-text');
const orbContainer = $('orb-container');
const searchResultsToggle = $('search-results-toggle');
const searchResultsWidget = $('search-results-widget');
const searchResultsClose  = $('search-results-close');
const searchResultsQuery  = $('search-results-query');
const searchResultsAnswer = $('search-results-answer');
const searchResultsList   = $('search-results-list');
const activityPanel       = $('activity-panel');
const activityToggle      = $('activity-toggle');
const activityClose       = $('activity-close');
const activityList        = $('activity-list');
const panelOverlay        = $('panel-overlay');
const settingsBtn         = $('settings-btn');
const monitorBtn          = $('monitor-btn');
const camBtn              = $('cam-btn');
const camPanel            = $('cam-panel');
const camVideo            = $('cam-video');
const camCanvas           = $('cam-canvas');
const camVisionModeInput  = $('cam-vision-mode');
const camMinimize         = $('cam-minimize');
const camClose            = $('cam-close');
const camPanelHeader      = $('cam-panel-header');
const camPanelResize      = $('cam-panel-resize');
const settingsPanel       = $('settings-panel');
const settingsClose       = $('settings-close');
const toggleAutoActivity  = $('toggle-auto-activity');
const toggleAutoSearch    = $('toggle-auto-search');
const toggleThinkingSounds = $('toggle-thinking-sounds');
const toastContainer     = $('toast-container');

class PreStarterPlayer {
    constructor() {
        this.audio = document.createElement('audio');
        this.audio.preload = 'auto';
    }
    play(onComplete) {
        const loaded = PRE_STARTER_PHRASES.filter(p => PRE_STARTER_CACHE[p]);
        if (loaded.length === 0) {
            if (onComplete) onComplete();
            return;
        }
        const phrase = loaded[Math.floor(Math.random() * loaded.length)];
        const base64 = PRE_STARTER_CACHE[phrase];
        if (!base64) {
            if (onComplete) onComplete();
            return;
        }
        this.audio.src = 'data:audio/mp3;base64,' + base64;
        this.audio.currentTime = 0;
        let fired = false;
        const done = () => {
            if (fired) return;
            fired = true;
            this.audio.onended = null;
            this.audio.onerror = null;
            if (onComplete) onComplete();
        };
        this.audio.onended = done;
        this.audio.onerror = done;
        const p = this.audio.play();
        if (p) p.catch(done);
    }
}

let preStarterPlayer = null;

class TTSPlayer {
    constructor() {
        this.queue = [];
        this.playing = false;
        this.enabled = true;
        this.stopped = false;
        this.audio = document.createElement('audio');
        this.audio.preload = 'auto';
    }
    unlock() {
        const silentWav = 'data:audio/wav;base64,UklGRigAAABXQVZFZm10IBIAAAABAAEARKwAAIhYAQACABAAAABkYXRhAgAAAAEA';
        this.audio.src = silentWav;
        const p = this.audio.play();
        if (p) p.catch(() => {});
        try {
            const ctx = new (window.AudioContext || window.webkitAudioContext)();
            const g = ctx.createGain();
            g.gain.value = 0;
            const o = ctx.createOscillator();
            o.connect(g);
            g.connect(ctx.destination);
            o.start(0);
            o.stop(ctx.currentTime + 0.001);
            setTimeout(() => ctx.close(), 200);
        } catch (_) {}
    }
    enqueue(base64Audio) {
        if (!this.enabled || this.stopped) return;
        this.queue.push(base64Audio);
        if (!this.playing) this._playLoop();
    }
    stop() {
        this.stopped = true;
        this.audio.pause();
        this.audio.removeAttribute('src');
        this.audio.load();
        this.queue = [];
        this.playing = false;
        if (ttsBtn) ttsBtn.classList.remove('tts-speaking');
        if (orbContainer) orbContainer.classList.remove('speaking');
        if (orb) orb.setState('idle');

        if (typeof this.onPlaybackComplete === 'function') this.onPlaybackComplete();
    }
    reset() {
        this.stop();
        this.stopped = false;
        this._loopId = (this._loopId || 0) + 1;
    }
    async _playLoop() {
        if (this.playing) return;
        this.playing = true;
        this._loopId = (this._loopId || 0) + 1;
        const myId = this._loopId;
        if (ttsBtn) ttsBtn.classList.add('tts-speaking');
        if (orbContainer) orbContainer.classList.add('speaking');
        if (orb) orb.setState('speaking');

        while (this.queue.length > 0) {
            if (this.stopped || myId !== this._loopId) break;
            const b64 = this.queue.shift();
            try {
                await this._playB64(b64);
            } catch (e) {
                console.warn('TTS segment error:', e);
            }
        }
        if (myId !== this._loopId) {
            this.playing = false;
            return;
        }
        this.playing = false;
        if (ttsBtn) ttsBtn.classList.remove('tts-speaking');
        if (orbContainer) orbContainer.classList.remove('speaking');
        if (orb) orb.setState('idle');

        if (typeof this.onPlaybackComplete === 'function') this.onPlaybackComplete();
    }
    _playB64(b64) {
        return new Promise(resolve => {
            this.audio.src = 'data:audio/mp3;base64,' + b64;
            const done = () => { resolve(); };
            this.audio.onended = done;
            this.audio.onerror = done;
            const p = this.audio.play();
            if (p) p.catch(done);
        });
    }
}

function init() {
    if (!chatMessages || !messageInput) {
        console.error('[JARVIS] Required DOM elements (chat-messages, message-input) not found.');
        return;
    }
    loadSettings();
    ttsPlayer = new TTSPlayer();
    if (ttsBtn) ttsBtn.classList.add('tts-active');
    setGreeting();
    initOrb();
    initOrbDashboard();
    // Apply saved orb config now that orb instance exists
    if (orb) {
        orb.applyGlobals(orbGlobals);
    }
    initPushToTalk();
    preloadStarterAudio();
    preStarterPlayer = new PreStarterPlayer();
    checkHealth();
    startBackgroundActivityPolling();
    playStartupBrief();
    bindEvents();
    initHistory();
    setMode(currentMode);
    autoResizeInput();
}

let startupBriefPlayed = false;
let startupBriefController = null;
let startupBriefDone = false;

/** Immediately stop the daily startup greeting (text stream + TTS audio) when
 *  the user interrupts by sending a message or starting to speak. Once stopped
 *  it will NOT resume. */
function stopStartupBrief() {
    startupBriefDone = true;
    if (startupBriefController) {
        try { startupBriefController.abort(); } catch (_) {}
        startupBriefController = null;
    }
    if (ttsPlayer && ttsPlayer.playing) {
        ttsPlayer.stop();
        ttsPlayer.stopped = false;
    }
}

function playStartupBrief() {
    if (startupBriefPlayed) return;
    startupBriefPlayed = true;
    startupBriefDone = false;
    startupBriefController = new AbortController();

    // Attach unlock to first interaction to bypass autoplay restrictions
    const unlockAndPlay = () => {
        if (ttsPlayer && ttsPlayer.unlock) {
            ttsPlayer.unlock();
        }
        document.removeEventListener('click', unlockAndPlay);
        document.removeEventListener('keydown', unlockAndPlay);
    };
    document.addEventListener('click', unlockAndPlay);
    document.addEventListener('keydown', unlockAndPlay);

    fetch(`${API}/api/startup-brief/stream`, { signal: startupBriefController.signal })
        .then(response => {
            // 204 = server says "already delivered this server session" (Ctrl+R)
            if (response.status === 204) {
                console.log('[JARVIS] Startup brief already played this server session — skipping.');
                return;
            }
            if (!response.ok) throw new Error('Startup brief fetch failed');
            
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            function readStream() {
                if (startupBriefDone) { try { reader.cancel(); } catch (_) {} return; }
                reader.read().then(({ done, value }) => {
                    if (done || startupBriefDone) return;
                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop();

                    for (const line of lines) {
                        if (line.startsWith('data: ')) {
                            try {
                                const data = JSON.parse(line.substring(6));
                                if (!startupBriefDone && data.audio && ttsPlayer) {
                                    ttsPlayer.enqueue(data.audio);
                                }
                            } catch (e) {
                                console.warn('Failed to parse SSE data', e);
                            }
                        }
                    }
                    readStream();
                }).catch(err => {
                    // stopStartupBrief() aborts this stream on purpose whenever
                    // the user interrupts -- by typing, speaking, or opening a
                    // past conversation. An intentional abort is not an error.
                    if (err && err.name === 'AbortError') return;
                    console.error("Error reading startup stream:", err);
                });
            }
            readStream();
        })
        .catch(err => console.error('Failed to start startup brief:', err));
}

async function preloadStarterAudio() {
    // Fetch each phrase through the normal /tts endpoint = the ONE voice cache.
    // First run: miss -> synthesized + saved on the server. After that: instant hit.
    const base = (typeof window !== 'undefined' && window.location.origin) ? window.location.origin : '';
    for (const phrase of PRE_STARTER_PHRASES) {
        try {
            const r = await fetch(`${base}/tts`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text: phrase }),
            });
            if (!r.ok) continue;
            const blob = await r.blob();
            const base64 = await new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onloadend = () => resolve((reader.result || '').split(',')[1] || '');
                reader.onerror = reject;
                reader.readAsDataURL(blob);
            });
            if (base64) PRE_STARTER_CACHE[phrase] = base64;
        } catch (_) {}
    }
}

function loadSettings() {
    try {
        const s = localStorage.getItem(SETTINGS_KEY);
        if (s) {
            const parsed = JSON.parse(s);
            settings = { ...DEFAULT_SETTINGS, ...parsed };
        }
        if (toggleAutoActivity) toggleAutoActivity.checked = settings.autoOpenActivity;
        if (toggleAutoSearch) toggleAutoSearch.checked = settings.autoOpenSearchResults;
        if (toggleThinkingSounds) toggleThinkingSounds.checked = settings.thinkingSounds;
        // Load orb config from localStorage
        orbDashLoad();
    } catch (_) {}
}

function saveSettings() {
    try {
        localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
    } catch (_) {}
}

/* ══════════════════════════════════════════════════════════════════
   Orb Customization Dashboard
   ══════════════════════════════════════════════════════════════════ */

const ORB_CONFIG_KEY = 'jarvis_orb_config';

// Default glow settings per state (CSS outer glow)
const ORB_DEFAULT_GLOWS = {
    idle:      { color: '#7c6aef', size: 8,  pulse: 0   },
    listening: { color: '#4cc2e9', size: 30, pulse: 1.0 },
    thinking:  { color: '#8c5aff', size: 25, pulse: 0.8 },
    searching: { color: '#00c8b4', size: 30, pulse: 1.2 },
    working:   { color: '#f0a03c', size: 35, pulse: 1.0 },
    speaking:  { color: '#7c6aef', size: 20, pulse: 1.6 },
};

// Runtime glow config (mutable, saved to localStorage)
let orbGlows = JSON.parse(JSON.stringify(ORB_DEFAULT_GLOWS));

// Runtime global config
let orbGlobals = { lerpRate: 6, baseHue: 0, orbSize: 600, idleOpacity: 0.35 };

// Dashboard state
let orbDashActive = false;
let orbDashCurrentState = 'idle';
const orbDashboard = $('orb-dashboard');
const orbDashBtn   = $('orb-dashboard-btn');
const orbDashClose = $('orb-dash-close');
const orbDashReset = $('orb-dash-reset');
const orbDashTabs  = $('orb-dash-tabs');

// Slider/control element IDs mapping
const ORB_SLIDER_MAP = {
    speed:     { key: 'speedMul',   fmt: v => parseFloat(v).toFixed(1) },
    noise:     { key: 'noiseMul',   fmt: v => parseFloat(v).toFixed(2) },
    glow:      { key: 'glowMul',    fmt: v => parseFloat(v).toFixed(2) },
    wave:      { key: 'waveAmp',    fmt: v => parseFloat(v).toFixed(2) },
    orbit:     { key: 'orbitSpeed',  fmt: v => parseFloat(v).toFixed(1) },
    rot:       { key: 'rotSpeed',    fmt: v => parseFloat(v).toFixed(2) },
    hue:       { key: 'hue',         fmt: v => v + '°' },
};

function initOrbDashboard() {
    if (!orbDashboard) return;

    // Open/close dashboard
    if (orbDashBtn) {
        orbDashBtn.addEventListener('click', () => {
            orbDashActive = !orbDashActive;
            orbDashboard.classList.toggle('open', orbDashActive);
            orbDashboard.setAttribute('aria-hidden', !orbDashActive);
            updatePanelOverlay();
            if (orbDashActive) {
                orbDashSelectState('idle');
            } else {
                // Return orb to idle when closing
                if (orb) orb.setState('idle');
            }
        });
    }

    if (orbDashClose) {
        orbDashClose.addEventListener('click', () => {
            orbDashActive = false;
            orbDashboard.classList.remove('open');
            orbDashboard.setAttribute('aria-hidden', 'true');
            updatePanelOverlay();
            if (orb) orb.setState('idle');
        });
    }

    // Tab clicks
    if (orbDashTabs) {
        orbDashTabs.querySelectorAll('.orb-dash-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                orbDashSelectState(tab.dataset.state);
            });
        });
    }

    // Global sliders
    _orbDashBindGlobal('orb-g-lerp', 'lerpRate', v => v);
    _orbDashBindGlobal('orb-g-hue', 'baseHue', v => v + '°');
    _orbDashBindGlobal('orb-g-size', 'orbSize', v => v);
    _orbDashBindGlobal('orb-g-opacity', 'idleOpacity', v => v);

    // Per-state sliders
    Object.keys(ORB_SLIDER_MAP).forEach(name => {
        const el = $('orb-s-' + name);
        const valEl = $('orb-s-' + name + '-val');
        if (!el) return;
        el.addEventListener('input', () => {
            const v = parseFloat(el.value);
            const info = ORB_SLIDER_MAP[name];
            ORB_STATES[orbDashCurrentState][info.key] = v;
            if (valEl) valEl.textContent = info.fmt(v);
            // Live preview: instant single-property update (no CSS thrashing)
            if (orb && orbDashActive) orb.setProperty(info.key, v);
            orbDashSave();
        });
    });

    // Glow color picker
    const glowColorEl = $('orb-s-glowcolor');
    const glowColorVal = $('orb-s-glowcolor-val');
    if (glowColorEl) {
        glowColorEl.addEventListener('input', () => {
            orbGlows[orbDashCurrentState].color = glowColorEl.value;
            if (glowColorVal) glowColorVal.textContent = glowColorEl.value;
            _orbDashApplyGlow(orbDashCurrentState);
            orbDashSave();
        });
    }

    // Glow size slider
    const glowSizeEl = $('orb-s-glowsize');
    const glowSizeVal = $('orb-s-glowsize-val');
    if (glowSizeEl) {
        glowSizeEl.addEventListener('input', () => {
            orbGlows[orbDashCurrentState].size = parseInt(glowSizeEl.value);
            if (glowSizeVal) glowSizeVal.textContent = glowSizeEl.value + 'px';
            _orbDashApplyGlow(orbDashCurrentState);
            orbDashSave();
        });
    }

    // Pulse speed slider
    const pulseEl = $('orb-s-pulse');
    const pulseVal = $('orb-s-pulse-val');
    if (pulseEl) {
        pulseEl.addEventListener('input', () => {
            orbGlows[orbDashCurrentState].pulse = parseFloat(pulseEl.value);
            if (pulseVal) pulseVal.textContent = parseFloat(pulseEl.value).toFixed(1) + 's';
            _orbDashApplyGlow(orbDashCurrentState);
            orbDashSave();
        });
    }

    // Reset defaults
    if (orbDashReset) {
        orbDashReset.addEventListener('click', () => {
            orbDashResetDefaults();
        });
    }
}

function _orbDashBindGlobal(elId, key, fmt) {
    const el = $(elId);
    const valEl = $(elId + '-val');
    if (!el) return;
    el.addEventListener('input', () => {
        const v = parseFloat(el.value);
        orbGlobals[key] = v;
        if (valEl) valEl.textContent = fmt(v);
        if (orb) orb.applyGlobals({ [key]: v });
        orbDashSave();
    });
}

function orbDashSelectState(stateName) {
    orbDashCurrentState = stateName;

    // Update tab active states
    if (orbDashTabs) {
        orbDashTabs.querySelectorAll('.orb-dash-tab').forEach(t => {
            t.classList.toggle('active', t.dataset.state === stateName);
        });
    }

    // Force orb into preview state (instant — no lerp delay)
    if (orb && orbDashActive) orb.setStateInstant(stateName);

    // Populate slider values from ORB_STATES
    const preset = ORB_STATES[stateName];
    if (!preset) return;

    Object.keys(ORB_SLIDER_MAP).forEach(name => {
        const el = $('orb-s-' + name);
        const valEl = $('orb-s-' + name + '-val');
        const info = ORB_SLIDER_MAP[name];
        if (el) el.value = preset[info.key];
        if (valEl) valEl.textContent = info.fmt(preset[info.key]);
    });

    // Populate glow controls
    const glow = orbGlows[stateName] || ORB_DEFAULT_GLOWS[stateName];
    const glowColorEl = $('orb-s-glowcolor');
    const glowColorVal = $('orb-s-glowcolor-val');
    const glowSizeEl = $('orb-s-glowsize');
    const glowSizeVal = $('orb-s-glowsize-val');
    const pulseEl = $('orb-s-pulse');
    const pulseVal = $('orb-s-pulse-val');

    if (glowColorEl) { glowColorEl.value = glow.color; }
    if (glowColorVal) { glowColorVal.textContent = glow.color; }
    if (glowSizeEl) { glowSizeEl.value = glow.size; }
    if (glowSizeVal) { glowSizeVal.textContent = glow.size + 'px'; }
    if (pulseEl) { pulseEl.value = glow.pulse; }
    if (pulseVal) { pulseVal.textContent = glow.pulse.toFixed(1) + 's'; }
}

function _orbDashApplyGlow(stateName) {
    // Dynamically update the CSS for this state's orb glow
    const glow = orbGlows[stateName];
    if (!glow || !orbContainer) return;

    // Build dynamic style rule for this state
    let styleEl = document.getElementById('orb-dash-dynamic-style');
    if (!styleEl) {
        styleEl = document.createElement('style');
        styleEl.id = 'orb-dash-dynamic-style';
        document.head.appendChild(styleEl);
    }

    // Rebuild all state glow rules
    let css = '';
    Object.keys(orbGlows).forEach(state => {
        const g = orbGlows[state];
        const rgba = _hexToRgba(g.color, 0.5);
        const rgbaLight = _hexToRgba(g.color, 0.2);
        const animRule = g.pulse > 0
            ? `animation: orbPulse ${g.pulse}s ease-in-out infinite;`
            : 'animation: none;';

        if (state === 'idle') {
            css += `#orb-container.orb-idle {
                opacity: var(--orb-idle-opacity, 0.35);
                ${animRule}
                filter: drop-shadow(0 0 ${g.size}px ${rgba});
            }\n`;
        } else {
            css += `#orb-container.orb-${state} {
                opacity: 1;
                ${animRule}
                filter: drop-shadow(0 0 ${g.size}px ${rgba})
                       drop-shadow(0 0 ${g.size * 2}px ${rgbaLight});
            }\n`;
        }
    });
    styleEl.textContent = css;
}

function _hexToRgba(hex, alpha) {
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function orbDashSave() {
    try {
        const config = {
            globals: { ...orbGlobals },
            states: JSON.parse(JSON.stringify(ORB_STATES)),
            glows: JSON.parse(JSON.stringify(orbGlows)),
        };
        localStorage.setItem(ORB_CONFIG_KEY, JSON.stringify(config));
    } catch (_) {}
}

function orbDashLoad() {
    try {
        const raw = localStorage.getItem(ORB_CONFIG_KEY);
        if (!raw) return;
        const config = JSON.parse(raw);

        // Restore globals
        if (config.globals) {
            orbGlobals = { ...orbGlobals, ...config.globals };
            // Apply to orb instance if ready
            if (orb) orb.applyGlobals(orbGlobals);

            // Update global slider UI
            const gLerp = $('orb-g-lerp');
            const gHue = $('orb-g-hue');
            const gSize = $('orb-g-size');
            const gOpacity = $('orb-g-opacity');
            if (gLerp) { gLerp.value = orbGlobals.lerpRate; const v = $('orb-g-lerp-val'); if (v) v.textContent = orbGlobals.lerpRate; }
            if (gHue) { gHue.value = orbGlobals.baseHue; const v = $('orb-g-hue-val'); if (v) v.textContent = orbGlobals.baseHue + '°'; }
            if (gSize) { gSize.value = orbGlobals.orbSize; const v = $('orb-g-size-val'); if (v) v.textContent = orbGlobals.orbSize; }
            if (gOpacity) { gOpacity.value = orbGlobals.idleOpacity; const v = $('orb-g-opacity-val'); if (v) v.textContent = orbGlobals.idleOpacity; }
        }

        // Restore per-state presets
        if (config.states) {
            Object.keys(config.states).forEach(state => {
                if (ORB_STATES[state]) {
                    Object.assign(ORB_STATES[state], config.states[state]);
                }
            });
        }

        // Restore glow settings
        if (config.glows) {
            Object.keys(config.glows).forEach(state => {
                if (orbGlows[state]) {
                    Object.assign(orbGlows[state], config.glows[state]);
                }
            });
            // Apply all glow rules
            _orbDashApplyGlow('idle');
        }
    } catch (e) {
        console.warn('[OrbDash] Failed to load config:', e);
    }
}

function orbDashResetDefaults() {
    // Reset ORB_STATES to defaults
    ORB_STATES = JSON.parse(JSON.stringify(ORB_DEFAULTS));
    // Reset glows
    orbGlows = JSON.parse(JSON.stringify(ORB_DEFAULT_GLOWS));
    // Reset globals
    orbGlobals = { lerpRate: 6, baseHue: 0, orbSize: 600, idleOpacity: 0.35 };

    // Apply to orb
    if (orb) {
        orb.applyGlobals(orbGlobals);
        orb.setStateInstant(orbDashCurrentState);
    }

    // Remove dynamic style
    const styleEl = document.getElementById('orb-dash-dynamic-style');
    if (styleEl) styleEl.textContent = '';

    // Remove orb container inline size override
    if (orbContainer) {
        orbContainer.style.width = '';
        orbContainer.style.height = '';
        orbContainer.style.removeProperty('--orb-idle-opacity');
    }

    // Clear localStorage
    try { localStorage.removeItem(ORB_CONFIG_KEY); } catch (_) {}

    // Refresh UI
    orbDashSelectState(orbDashCurrentState);

    // Reset global sliders
    const gLerp = $('orb-g-lerp'); if (gLerp) gLerp.value = 6;
    const gHue = $('orb-g-hue'); if (gHue) gHue.value = 0;
    const gSize = $('orb-g-size'); if (gSize) gSize.value = 600;
    const gOpacity = $('orb-g-opacity'); if (gOpacity) gOpacity.value = 0.35;
    const gLerpVal = $('orb-g-lerp-val'); if (gLerpVal) gLerpVal.textContent = '6';
    const gHueVal = $('orb-g-hue-val'); if (gHueVal) gHueVal.textContent = '0°';
    const gSizeVal = $('orb-g-size-val'); if (gSizeVal) gSizeVal.textContent = '600';
    const gOpacityVal = $('orb-g-opacity-val'); if (gOpacityVal) gOpacityVal.textContent = '0.35';
}


function setGreeting() {
    const h = new Date().getHours();
    let g = 'Good evening.';
    if (h < 12) g = 'Good morning.';
    else if (h < 17) g = 'Good afternoon.';
    else if (h >= 22) g = 'Burning the midnight oil?';
    if (welcomeTitle) welcomeTitle.textContent = g;
}

function initOrb() {
    if (typeof OrbRenderer === 'undefined') return;
    try {
        orb = new OrbRenderer(orbContainer, {
            hue: 0,
            hoverIntensity: 0.3,
            backgroundColor: [0.02, 0.02, 0.06]
        });

        // Auto-update status badge whenever orb state changes
        const _origSetState = orb.setState.bind(orb);
        const _origSetStateInstant = orb.setStateInstant.bind(orb);

        orb.setState = (name) => {
            _origSetState(name);
            // Don't update badge during dashboard preview
            if (!orbDashActive) updateStatusBadge(name);
        };
        orb.setStateInstant = (name) => {
            _origSetStateInstant(name);
            if (!orbDashActive) updateStatusBadge(name);
        };
    } catch (e) { console.warn('Orb init failed:', e); }
}


/* ══════════════════════════════════════════════════════════════════
   Push-to-Talk  (Ctrl+Shift hold → record → release → send)
   HYBRID: MediaRecorder (reliable) + Web Speech API (live preview)
   ══════════════════════════════════════════════════════════════════ */

/** Shared AbortController so PTT can abort an in-flight stream */
let pttStreamController = null;
let mediaRecorder = null;
let audioChunks = [];
let micStream = null;

function initPushToTalk() {
    // ── Keyboard event listeners ──
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Control') ctrlHeld = true;
        if (e.key === 'Shift') shiftHeld = true;

        if (ctrlHeld && shiftHeld && !isRecording) {
            e.preventDefault();
            pttStartRecording();
        }
        if (ctrlHeld && shiftHeld) {
            e.preventDefault();
        }
    });

    document.addEventListener('keyup', (e) => {
        if (e.key === 'Control') ctrlHeld = false;
        if (e.key === 'Shift') shiftHeld = false;

        if ((!ctrlHeld || !shiftHeld) && isRecording) {
            pttStopRecording();
        }
    });

    window.addEventListener('blur', () => {
        ctrlHeld = false;
        shiftHeld = false;
        if (isRecording) pttStopRecording();
    });

    if (micBtn) micBtn.title = 'Voice input — hold Ctrl+Shift to speak';
    console.log('[PTT] Hybrid Push-to-Talk initialized (MediaRecorder + Web Speech API)');
}

/** Start BOTH MediaRecorder and Web Speech API in parallel */
async function pttStartRecording() {
    if (isRecording) return;

    // ── Interrupt the daily startup greeting if it's playing ──
    stopStartupBrief();

    // ── Interrupt streaming if active ──
    if (isStreaming && pttStreamController) {
        console.log('[PTT] Interrupting active stream...');
        pttStreamController.abort();
    }

    // ── Interrupt TTS if playing ──
    if (ttsPlayer && ttsPlayer.playing) {
        ttsPlayer.stop();
        ttsPlayer.stopped = false;
    }
    // Stop pre-starter audio
    if (preStarterPlayer && preStarterPlayer.audio && !preStarterPlayer.audio.paused) {
        preStarterPlayer.audio.pause();
        preStarterPlayer.audio.currentTime = 0;
    }

    isRecording = true;
    if (orb) orb.setState('listening');
    currentTranscript = '';
    finalReceived = false;
    pttSendDone = false;
    audioChunks = [];
    clearTimeout(pttSafetyTimer);

    if (messageInput) {
        messageInput.value = '';
        messageInput.disabled = false;
    }
    pttUpdateUI(true);

    // ── 1. Start MediaRecorder (RELIABLE — captures from this exact moment) ──
    try {
        // Always get a fresh stream — this way we release the mic as soon as recording stops
        micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        mediaRecorder = new MediaRecorder(micStream, {
            mimeType: MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
                ? 'audio/webm;codecs=opus'
                : 'audio/webm'
        });
        mediaRecorder.ondataavailable = (e) => {
            if (e.data && e.data.size > 0) audioChunks.push(e.data);
        };
        mediaRecorder.start(100); // collect chunks every 100ms
        console.log('[PTT] MediaRecorder started');
    } catch (err) {
        console.warn('[PTT] MediaRecorder failed:', err);
        showToast('Microphone access denied. Please allow in browser settings.');
        isRecording = false;
        pttUpdateUI(false);
        return;
    }

    // ── 2. Start Web Speech API (BONUS — live preview, might fail) ──
    try {
        // Kill previous instance
        if (recognition) {
            try { recognition.onresult = null; recognition.onerror = null; recognition.onend = null; } catch (_) {}
            try { recognition.abort(); } catch (_) {}
        }
        const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (SR) {
            recognition = new SR();
            recognition.continuous = false;  // single utterance — prevents duplicate results
            recognition.interimResults = true;
            recognition.maxAlternatives = 1;
            recognition.lang = 'en-IN';

            recognition.onresult = (e) => {
                if (pttSendDone) return;  // already sent, ignore further results
                if (!e.results || e.results.length === 0) return;
                let full = '';
                for (let i = 0; i < e.results.length; i++) {
                    full += e.results[i][0].transcript;
                }
                currentTranscript = full;
                const latest = e.results[e.results.length - 1];
                if (latest.isFinal) {
                    finalReceived = true;
                    if (!isRecording && !pttSendDone) {
                        pttSendTranscript();
                    }
                }
                // Live preview
                if (messageInput && !pttSendDone) messageInput.value = currentTranscript;
            };
            recognition.onerror = () => {}; // silent — MediaRecorder is the backup
            recognition.onend = () => {
                if (!pttSendDone && currentTranscript.trim() && !isRecording) {
                    pttSendTranscript();
                }
            };
            recognition.start();
            console.log('[PTT] Web Speech API started (bonus preview)');
        }
    } catch (_) {
        console.log('[PTT] Web Speech API unavailable — MediaRecorder will handle it');
    }
}

function pttStopRecording() {
    if (!isRecording) return;
    isRecording = false;
    pttUpdateUI(false);
    if (orb) orb.setState('thinking');

    // Stop MediaRecorder — collect final chunk
    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
        mediaRecorder.stop();
        console.log('[PTT] MediaRecorder stopped');
    }

    // Release mic stream so OS mic indicator disappears immediately
    if (micStream) {
        micStream.getTracks().forEach(t => t.stop());
        micStream = null;
    }

    // Don't stop Web Speech API immediately — let it finish processing
    // (but it's just a bonus, MediaRecorder is the reliable backup)

    // If Web Speech already has a final result → send instantly (fast path)
    if (finalReceived && currentTranscript.trim() && !pttSendDone) {
        console.log('[PTT] Fast path: Web Speech has final text, sending...');
        pttSendTranscript();
        return;
    }

    // Show processing hint
    if (messageInput && !currentTranscript.trim()) {
        messageInput.placeholder = 'Processing audio...';
    }

    // Wait 1.5s for Web Speech API to produce final text
    // If it doesn't → fall back to MediaRecorder + backend Whisper
    pttSafetyTimer = setTimeout(() => {
        if (pttSendDone) return;

        // Stop Web Speech API
        if (recognition) {
            try { recognition.stop(); } catch (_) {}
        }

        if (currentTranscript.trim()) {
            // Web Speech produced something (even interim) — use it
            console.log('[PTT] Using Web Speech interim text');
            pttSendTranscript();
        } else {
            // Web Speech failed completely → send audio to backend Whisper
            console.log('[PTT] Web Speech failed — falling back to Whisper...');
            pttSendAudioToBackend();
        }
    }, 1500);
}

/** FAST PATH: Web Speech API got the text — send immediately */
function pttSendTranscript() {
    clearTimeout(pttSafetyTimer);
    if (pttSendDone) return;
    pttSendDone = true;

    // Stop everything
    if (recognition) { try { recognition.stop(); } catch (_) {} }

    const text = currentTranscript.trim();
    currentTranscript = '';
    finalReceived = false;

    if (messageInput) {
        messageInput.placeholder = 'Message Jarvis...';
        messageInput.value = '';
    }

    if (!text) return;

    console.log('[PTT] Sending (Web Speech):', text.substring(0, 80));
    pttVoiceSource = 'web-speech';
    sendMessage(text);
}

/** FALLBACK PATH: Send recorded audio blob to backend for Whisper transcription */
async function pttSendAudioToBackend() {
    if (pttSendDone) return;
    pttSendDone = true;

    if (messageInput) messageInput.placeholder = 'Transcribing...';

    // Build audio blob from recorded chunks
    const blob = new Blob(audioChunks, { type: mediaRecorder?.mimeType || 'audio/webm' });
    audioChunks = [];

    if (blob.size < 100) {
        console.log('[PTT] Audio too small, ignoring');
        if (messageInput) messageInput.placeholder = 'Message Jarvis...';
        return;
    }

    console.log('[PTT] Sending audio to backend: %d bytes', blob.size);

    try {
        const formData = new FormData();
        formData.append('file', blob, 'audio.webm');

        const res = await fetch(`${API}/transcribe`, {
            method: 'POST',
            body: formData,
        });

        if (!res.ok) {
            throw new Error(`Transcription failed: HTTP ${res.status}`);
        }

        const data = await res.json();
        const text = (data.text || '').trim();

        if (messageInput) {
            messageInput.placeholder = 'Message Jarvis...';
            messageInput.value = '';
        }

        if (!text) {
            console.log('[PTT] Whisper returned empty text');
            showToast('Could not understand audio. Try speaking louder.');
            return;
        }

        console.log('[PTT] Sending (Whisper):', text.substring(0, 80));
        pttVoiceSource = 'whisper';
        sendMessage(text);
    } catch (err) {
        console.error('[PTT] Backend transcription failed:', err);
        showToast('Transcription failed. Please try again.');
        if (messageInput) messageInput.placeholder = 'Message Jarvis...';
    }
}

function pttUpdateUI(active) {
    if (!micBtn) return;
    if (active) {
        micBtn.classList.add('listening');
    } else {
        micBtn.classList.remove('listening');
    }
}


const CAM_BYPASS_TOKEN = 'TTCAMTOKENTT';
const CAMERA_QUERY_PATTERNS = [
    /what\s+(can|do)\s+you\s+see/i,
    /can\s+you\s+see/i,
    /describe\s+(what\s+you\s+see|this|the\s+image)/i,
    /what('s|s)\sss+in\sss+(this\sss+)?(picture|image)/i,
    /what\s+do\s+i\s+look\s+like/i,
    /what\s+(am\s+i\s+)?holding/i,
    /show\s+me\s+what\s+you\s+see/i,
];
function isCameraQuery(text) {
    if (!text || typeof text !== 'string') return false;
    const t = text.trim().toLowerCase();
    return CAMERA_QUERY_PATTERNS.some(r => r.test(t)) ||
        (t.includes('see') && (t.includes('what') || t.includes('describe')));
}

function startCamera() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        showToast('Camera not supported in this browser.');
        return Promise.reject(new Error('Camera not supported'));
    }
    if (camStream) return Promise.resolve();
    return navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user' }, audio: false })
        .then(stream => {
            camStream = stream;
            if (camVideo) camVideo.srcObject = stream;
            if (camPanel) { camPanel.classList.add('visible'); camPanel.setAttribute('aria-hidden', 'false'); }
            if (camBtn) {
                camBtn.classList.add('cam-active');
                camBtn.title = 'Camera on — click to turn off';
                const icon = camBtn.querySelector('.cam-icon');
                const iconActive = camBtn.querySelector('.cam-icon-active');
                if (icon) icon.style.display = 'none';
                if (iconActive) iconActive.style.display = '';
            }
        })
        .catch(err => {
            showToast('Camera access denied. ' + (err.message || ''));
            throw err;
        });
}

function stopCamera() {
    if (camStream) {
        camStream.getTracks().forEach(t => t.stop());
        camStream = null;
    }
    if (camVideo) camVideo.srcObject = null;
    if (camPanel) { camPanel.classList.remove('visible'); camPanel.setAttribute('aria-hidden', 'true'); }
    if (camVisionModeInput) camVisionModeInput.checked = false;
    if (camBtn) {
        camBtn.classList.remove('cam-active');
        camBtn.title = 'Camera — capture and send for vision';
        const icon = camBtn.querySelector('.cam-icon');
        const iconActive = camBtn.querySelector('.cam-icon-active');
        if (icon) icon.style.display = '';
        if (iconActive) iconActive.style.display = 'none';
    }
}

function initCameraPanel() {
    if (!camPanel) return;
    let dragStart = { x: 0, y: 0, left: 0, top: 0 };
    let resizeStart = { x: 0, y: 0, w: 0, h: 0 };
    if (camClose) camClose.addEventListener('click', () => stopCamera());
    if (camMinimize) camMinimize.addEventListener('click', () => {
        camPanel.classList.toggle('minimized');
    });
    if (camPanelHeader) {
        camPanelHeader.addEventListener('mousedown', (e) => {
            if (e.target.closest('.cam-panel-btn, .cam-panel-vision-mode')) return;
            e.preventDefault();
            const r = camPanel.getBoundingClientRect();
            dragStart = { x: e.clientX, y: e.clientY, left: r.left, top: r.top };
            const onMove = (ev) => {
                const dx = ev.clientX - dragStart.x;
                const dy = ev.clientY - dragStart.y;
                camPanel.style.left = (dragStart.left + dx) + 'px';
                camPanel.style.top = (dragStart.top + dy) + 'px';
                camPanel.style.right = 'auto';
                camPanel.style.bottom = 'auto';
            };
            const onUp = () => { document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp); };
            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', onUp);
        });
    }
    if (camPanelResize) {
        camPanelResize.addEventListener('mousedown', (e) => {
            e.preventDefault();
            e.stopPropagation();
            const r = camPanel.getBoundingClientRect();
            resizeStart = { x: e.clientX, y: e.clientY, w: r.width, h: r.height };
            const onMove = (ev) => {
                const dw = ev.clientX - resizeStart.x;
                const dh = ev.clientY - resizeStart.y;
                const nw = Math.max(200, Math.min(window.innerWidth, resizeStart.w + dw));
                const nh = Math.max(150, Math.min(window.innerHeight * 0.7, resizeStart.h + dh));
                camPanel.style.width = nw + 'px';
                camPanel.style.height = nh + 'px';
            };
            const onUp = () => { document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp); };
            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', onUp);
        });
    }
    camPanel.addEventListener('dblclick', (e) => {
        if (e.target.closest('.cam-panel-header') && !e.target.closest('.cam-panel-btn, .cam-panel-vision-mode')) {
            camPanel.classList.toggle('minimized');
        }
    });
    camPanel.querySelector('.cam-panel-body')?.addEventListener('click', (e) => {
        if (camPanel.classList.contains('minimized')) camPanel.classList.remove('minimized');
    });
}

function handleActions(actions, contentEl) {
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
            const img = document.createElement('img');
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
                        sendMessageWithImage(resendMsg, frame);
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
        if (p.action === 'open' || p.action === 'refresh') openRemindersPanel();
        else if (p.action === 'close') closeRemindersPanel();
    }
    if (panelActions.notes) {
        const p = panelActions.notes;
        if (p.action === 'open' || p.action === 'refresh') openNotesPanel(p.tab || 'notes');
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

// handleBackgroundTasks / pollBackgroundTask / updateTaskCard removed (agent rebuild).
// Images and content now arrive inline via _actions → handleActions().


function captureFrameAsBase64() {
    if (!camVideo || !camStream || camVideo.readyState < 2) return null;
    if (!camCanvas) return null;
    const w = camVideo.videoWidth;
    const h = camVideo.videoHeight;
    if (!w || !h || w < 64 || h < 64) return null;
    camCanvas.width = w;
    camCanvas.height = h;
    const ctx = camCanvas.getContext('2d');
    if (!ctx) return null;
    ctx.drawImage(camVideo, 0, 0, w, h);
    try {
        return camCanvas.toDataURL('image/jpeg', 0.85).split(',')[1];
    } catch (_) {
        return null;
    }
}

async function captureFrameAsBase64Safe() {
    if (!camVideo || !camStream || !camCanvas) return null;
    return new Promise((resolve) => {
        const doCapture = () => {
            const w = camVideo.videoWidth;
            const h = camVideo.videoHeight;
            if (!w || !h || w < 64 || h < 64) {
                resolve(null);
                return;
            }
            camCanvas.width = w;
            camCanvas.height = h;
            const ctx = camCanvas.getContext('2d');
            if (!ctx) { resolve(null); return; }
            ctx.drawImage(camVideo, 0, 0, w, h);
            try {
                const b64 = camCanvas.toDataURL('image/jpeg', 0.9).split(',')[1];
                resolve(b64);
            } catch (_) {
                resolve(null);
            }
        };
        if (camVideo.readyState < 2) {
            const onReady = () => { camVideo.removeEventListener('loadeddata', onReady); doCapture(); };
            camVideo.addEventListener('loadeddata', onReady);
            setTimeout(() => { camVideo.removeEventListener('loadeddata', onReady); doCapture(); }, 3000);
            return;
        }
        const w = camVideo.videoWidth;
        const h = camVideo.videoHeight;
        if (w && h && w >= 64 && h >= 64) {
            if (typeof camVideo.requestVideoFrameCallback === 'function') {
                camVideo.requestVideoFrameCallback(() => { doCapture(); });
            } else {
                setTimeout(doCapture, 150);
            }
        } else {
            setTimeout(() => {
                const w2 = camVideo.videoWidth || 0;
                const h2 = camVideo.videoHeight || 0;
                if (w2 && h2 && w2 >= 64 && h2 >= 64) doCapture();
                else resolve(null);
            }, 300);
        }
    });
}

async function sendMessageWithImage(text, imgBase64) {
    if (!text || !imgBase64 || isStreaming) return;
    stopStartupBrief();
    const messageToSend = text + ' ' + CAM_BYPASS_TOKEN;
    addMessage('user', text);
    addTypingIndicator();
    isStreaming = true;
    if (sendBtn) sendBtn.disabled = true;
    if (messageInput) messageInput.disabled = true;
    if (orbContainer) orbContainer.classList.add('active');
    if (ttsPlayer) { ttsPlayer.reset(); ttsPlayer.unlock(); }
    let timeoutId = null;
    const controller = new AbortController();
    try {
        timeoutId = setTimeout(() => controller.abort(), 300000);
        const res = await fetch(`${API}/chat/jarvis/stream`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: messageToSend,
                session_id: sessionId,
                tts: !!(ttsPlayer && ttsPlayer.enabled),
                imgbase64: imgBase64,
            }),
            signal: controller.signal,
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        removeTypingIndicator();
        const contentEl = addMessage('assistant', '');
        contentEl.innerHTML = '<span class="msg-stream-text">...</span>';
        scrollToBottom();
        if (!res.body) throw new Error('No response body');
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let sseBuffer = '';
        let fullResponse = '';
        let cursorEl = null;
        let streamDone = false;
        while (!streamDone) {
            const { done, value } = await reader.read();
            if (done) break;
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
                        if (activityPanel && settings.autoOpenActivity) { activityPanel.classList.add('open'); updatePanelOverlay(); }
                    }
                    if (data.actions) handleActions(data.actions, contentEl);
                    if ('chunk' in data) {
                        const chunkText = data.chunk || '';
                        fullResponse += chunkText;
                        const textSpan = contentEl.querySelector('.msg-stream-text');
                        if (textSpan) {
                            textSpan.textContent = fullResponse;
                            textSpan.classList.remove('stream-placeholder');
                        }
                        if (!cursorEl) {
                            cursorEl = document.createElement('span');
                            cursorEl.className = 'stream-cursor';
                            cursorEl.textContent = '|';
                            contentEl.appendChild(cursorEl);
                        }
                        scrollToBottom();
                    }
                    if (data.audio && ttsPlayer) ttsPlayer.enqueue(data.audio);
                    if (data.error) throw new Error(data.error);
                    if (data.done) { streamDone = true; break; }
                } catch (parseErr) {
                    if (parseErr.message && !parseErr.message.includes('JSON')) throw parseErr;
                }
            }
            if (streamDone) break;
        }
        if (cursorEl) cursorEl.remove();
        const textSpan = contentEl.querySelector('.msg-stream-text');
        if (textSpan && !fullResponse) textSpan.textContent = '(No response)';
    } catch (err) {
        clearTimeout(timeoutId);
        removeTypingIndicator();
        addMessage('assistant', 'Something went wrong analyzing the image. Please try again.');
    } finally {
        clearTimeout(timeoutId);
        isStreaming = false;
        if (sendBtn) sendBtn.disabled = false;
        if (messageInput) messageInput.disabled = false;
        if (orbContainer) orbContainer.classList.remove('active');
        if (orb) orb.setState('idle');
    }
}

async function checkHealth() {
    try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 5000);
        const r = await fetch(`${API}/health`, { signal: controller.signal });
        clearTimeout(timeoutId);
        const d = await r.json().catch(() => null);
        const ok = d && (d.status === 'healthy' || d.status === 'degraded');
        if (statusDot) statusDot.classList.toggle('offline', !ok);
        if (!ok) {
            if (statusText) statusText.textContent = 'Offline';
        } else {
            // Only reset to 'Online' if the orb is idle — don't overwrite active state labels
            const currentOrbState = orb ? orb.stateName : 'idle';
            if (currentOrbState === 'idle') {
                if (statusText) statusText.textContent = 'Online';
            }
        }
    } catch (e) {
        if (statusDot) statusDot.classList.add('offline');
        if (statusText) statusText.textContent = 'Offline';
        if (typeof console !== 'undefined' && console.warn) console.warn('[Health] Check failed:', e);
    }
}

/* ── Status Badge: show current orb state instead of "Online" ── */
const ORB_STATE_LABELS = {
    idle:      'Online',
    listening: 'Listening',
    thinking:  'Thinking',
    searching: 'Searching',
    working:   'Working',
    speaking:  'Speaking',
};

function updateStatusBadge(stateName) {
    if (statusText) {
        statusText.textContent = ORB_STATE_LABELS[stateName] || 'Online';
    }
    if (statusDot) {
        // Remove all state-* classes
        statusDot.classList.remove(
            'state-listening', 'state-thinking', 'state-searching',
            'state-working', 'state-speaking'
        );
        if (stateName !== 'idle') {
            statusDot.classList.add('state-' + stateName);
        }
    }
}

function showToast(msg, durationMs = 5000) {
    if (!toastContainer || !msg) return;
    const el = document.createElement('div');
    el.className = 'toast';
    el.textContent = msg;
    toastContainer.appendChild(el);
    el.offsetHeight;
    el.classList.add('toast-visible');
    const t = setTimeout(() => {
        el.classList.remove('toast-visible');
        setTimeout(() => el.remove(), 300);
    }, durationMs);
    el.addEventListener('click', () => { clearTimeout(t); el.classList.remove('toast-visible'); setTimeout(() => el.remove(), 300); });
}

function bindEvents() {
    if (sendBtn) sendBtn.addEventListener('click', () => { if (!isStreaming) sendMessage(); });
    if (messageInput) messageInput.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (!isStreaming) sendMessage(); }
    });
    if (messageInput) messageInput.addEventListener('input', () => {
        autoResizeInput();
        const len = messageInput.value.length;
        if (charCount) charCount.textContent = len > 100 ? `${len.toLocaleString()} / 32,000` : '';
    });
    if (camBtn) camBtn.addEventListener('click', () => {
        if (camStream) stopCamera();
        else startCamera();
    });
    initCameraPanel();
    if (micBtn) micBtn.addEventListener('click', () => {
        showToast('Hold Ctrl+Shift to speak', 3000);
    });
    if (ttsBtn) ttsBtn.addEventListener('click', () => {
        if (ttsPlayer) ttsPlayer.enabled = !ttsPlayer.enabled;
        ttsBtn.classList.toggle('tts-active', ttsPlayer && ttsPlayer.enabled);
        if (ttsPlayer && !ttsPlayer.enabled) ttsPlayer.stop();
    });
    if (newChatBtn) newChatBtn.addEventListener('click', newChat);
    if (btnJarvis) btnJarvis.addEventListener('click', () => setMode('jarvis'));
    document.querySelectorAll('.chip').forEach(c => {
        c.addEventListener('click', () => { if (!isStreaming) sendMessage(c.dataset.msg); });
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
            settings.autoOpenActivity = toggleAutoActivity.checked;
            saveSettings();
        });
    }
    if (toggleAutoSearch) {
        toggleAutoSearch.addEventListener('change', () => {
            settings.autoOpenSearchResults = toggleAutoSearch.checked;
            saveSettings();
        });
    }
    if (toggleThinkingSounds) {
        toggleThinkingSounds.addEventListener('change', () => {
            settings.thinkingSounds = toggleThinkingSounds.checked;
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

function autoResizeInput() {
    messageInput.style.height = 'auto';
    messageInput.style.height = Math.min(messageInput.scrollHeight, 120) + 'px';
}

function updatePanelOverlay() {
    if (!panelOverlay) return;
    const anyOpen = (activityPanel && activityPanel.classList.contains('open')) ||
        (searchResultsWidget && searchResultsWidget.classList.contains('open')) ||
        (settingsPanel && settingsPanel.classList.contains('open')) ||
        (orbDashboard && orbDashboard.classList.contains('open'));
    panelOverlay.classList.toggle('visible', !!anyOpen);
}

function setMode(mode) {
    currentMode = mode || 'jarvis';
    if (btnJarvis) btnJarvis.classList.add('active');
    if (modeSlider) modeSlider.classList.remove('center', 'right');
    if (activityToggle) activityToggle.style.display = '';
}

function newChat(opts = {}) {
    if (isRecording) pttStopRecording();
    if (ttsPlayer) ttsPlayer.stop();
    if (camStream) stopCamera();
    // The previous conversation stays on disk and in History; only the active
    // pointer is cleared. The new chat has no id until the first reply comes
    // back, so the URL drops back to the base path.
    sessionId = null;
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

function createWelcome() {
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
        c.addEventListener('click', () => { if (!isStreaming) sendMessage(c.dataset.msg); });
    });
    return div;
}

function isUrlLike(str) {
    if (!str || typeof str !== 'string') return false;
    const s = str.trim();
    return s.length > 40 && (/^https?:\/\//i.test(s));
}

function friendlyUrlLabel(url) {
    if (!url || typeof url !== 'string') return 'View source';
    try {
        const u = new URL(url.startsWith('http') ? url : 'https://' + url);
        const host = u.hostname.replace(/^www\./, '');
        const path = u.pathname !== '/' ? u.pathname.slice(0, 20) + (u.pathname.length > 20 ? '…' : '') : '';
        return path ? host + path : host;
    } catch (_) {
        return url.length > 40 ? url.slice(0, 37) + '…' : url;
    }
}

function truncateSnippet(text, maxLen) {
    if (!text || typeof text !== 'string') return '';
    const t = text.trim();
    if (t.length <= maxLen) return t;
    return t.slice(0, maxLen).trim() + '…';
}

function renderSearchResults(payload) {
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

function safeUrlForHref(url) {
    if (!url || typeof url !== 'string') return '';
    const u = url.trim();
    if (u.startsWith('https://') || u.startsWith('http://')) return escapeAttr(u);
    return '';
}

function escapeAttr(str) {
    if (typeof str !== 'string') return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/"/g, '&quot;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

const ACTIVITY_STEPS = {
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

function appendActivity(activity) {
    if (!activityList || !activity) return;
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
        const okTxt = activity.ok === false ? '✗ failed' : '✓ done';
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

function startBackgroundActivityPolling() {
    const poll = async () => {
        if (document.hidden) return;
        try {
            const response = await fetch(`${API}/api/activity/recent`, { cache: 'no-store' });
            if (!response.ok) return;
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
                appendActivity(event.activity);
                if (event.correction) addCorrectionMessage(event.correction);
            }
        } catch (_) {
            // Background visibility is optional and must never affect chat.
        }
    };
    poll();
    window.setInterval(poll, 2000);
}

function escapeHtml(str) {
    if (typeof str !== 'string') return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function hideWelcome() {
    const w = document.getElementById('welcome-screen');
    if (w) w.remove();
}

const AVATAR_ICON_USER = '<svg class="msg-avatar-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>';
const AVATAR_ICON_ASSISTANT = '<svg class="msg-avatar-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="2"/><path d="M12 7v4"/><circle cx="9" cy="16" r="1" fill="currentColor"/><circle cx="15" cy="16" r="1" fill="currentColor"/></svg>';

function addMessage(role, text) {
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
        ? `Jarvis  (${currentMode === 'jarvis' ? 'Jarvis' : currentMode === 'realtime' ? 'Realtime' : 'General'})`
        : 'You';
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

// Verification finishes after the reply has already streamed, so a FAIL lands
// once Jarvis has said "done". Showing it only in the side activity panel meant
// the user was never actually told the action had not worked.
function addCorrectionMessage(text) {
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

function addTypingIndicator() {
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
    label.textContent = `Jarvis  (${currentMode === 'jarvis' ? 'Jarvis' : currentMode === 'realtime' ? 'Realtime' : 'General'})`;
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

function removeTypingIndicator() {
    const t = document.getElementById('typing-msg');
    if (t) t.remove();
}

function scrollToBottom() {
    requestAnimationFrame(() => {
        chatMessages.scrollTop = chatMessages.scrollHeight;
    });
}

async function sendMessage(textOverride) {
    let text = (textOverride || messageInput.value).trim();
    const visionModeOn = camVisionModeInput && camVisionModeInput.checked;
    const wantsCamera = visionModeOn || isCameraQuery(text) || (camStream && text);
    if (wantsCamera && !text) text = 'What do you see?';
    if (!text || isStreaming) return;
    // User is interrupting the startup greeting by typing a command — kill it for good.
    stopStartupBrief();
    if ((isCameraQuery(text) || visionModeOn) && !camStream) {
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
    let imgBase64 = null;
    if (camStream && wantsCamera) {
        imgBase64 = await captureFrameAsBase64Safe();
        if (!imgBase64) showToast('Camera frame not ready. Please try again.');
    }
    messageInput.value = '';
    autoResizeInput();
    charCount.textContent = '';
    addMessage('user', text);
    addTypingIndicator();
    isStreaming = true;
    if (sendBtn) sendBtn.disabled = true;
    if (messageInput) messageInput.disabled = true;
    if (orbContainer) orbContainer.classList.add('active');
    if (orb) orb.setState('thinking');
    if (ttsPlayer) { ttsPlayer.reset(); ttsPlayer.unlock(); }
    const messageToSend = imgBase64 ? (text + ' ' + CAM_BYPASS_TOKEN) : text;
    const endpoint = '/chat/jarvis/stream';
    if (activityList) {
        activityList.innerHTML = '<div class="activity-empty" id="activity-empty">Processing...</div>';
        // Show voice input source if this message came from PTT
        if (pttVoiceSource) {
            appendActivity({ event: 'voice_input', source: pttVoiceSource });
            pttVoiceSource = null;
        }
        if (activityToggle) activityToggle.style.display = '';
        if (activityPanel && settings.autoOpenActivity) { activityPanel.classList.add('open'); updatePanelOverlay(); }
    }
    let firstChunkReceived = false;
    let timeoutId = null;
    const controller = new AbortController();
    pttStreamController = controller;  // expose to PTT for interrupt
    try {
        // Starter audio is now triggered by the 'decision' activity event
        // when query_type is 'realtime' (Serper search needed)
        timeoutId = setTimeout(() => controller.abort(), 300000);
        const res = await fetch(`${API}${endpoint}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: messageToSend,
                session_id: sessionId,
                tts: !!(ttsPlayer && ttsPlayer.enabled),
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
        const contentEl = addMessage('assistant', '');
        contentEl.innerHTML = '<span class="msg-stream-text">...</span>';
        scrollToBottom();
        if (!res.body) throw new Error('No response body');
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let sseBuffer = '';
        let fullResponse = '';
        let cursorEl = null;
        let streamDone = false;
        while (!streamDone) {
            const { done, value } = await reader.read();
            if (done) break;
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
                        if (activityPanel && settings.autoOpenActivity) { activityPanel.classList.add('open'); updatePanelOverlay(); }
                        // Play starter audio ONLY when brain decides this is a realtime (web search) query
                        if (data.activity.event === 'decision' && data.activity.query_type === 'realtime') {
                            if (orb) orb.setState('searching');
                            if (ttsPlayer?.enabled && settings.thinkingSounds && preStarterPlayer) {
                                preStarterPlayer.play(() => {
                                });
                            }
                        }
                        // ── Orb state transitions from SSE activity events ──
                        if (data.activity.event === 'decision' && data.activity.query_type === 'task') {
                            if (orb) orb.setState('working');
                        }
                        if (data.activity.event === 'searching_web' || data.activity.event === 'extracting_query') {
                            if (orb) orb.setState('searching');
                        }
                        if (data.activity.event === 'tool_call') {
                            if (orb) orb.setState('working');
                        }
                        if (data.activity.event === 'first_chunk') {
                            if (orb && !(ttsPlayer && ttsPlayer.enabled)) orb.setState('speaking');
                        }
                        if (data.activity.event === 'agent_done' || data.activity.event === 'execution_completed') {
                            // Only go to speaking if TTS is active, otherwise wait for stream done
                            if (orb && ttsPlayer && ttsPlayer.enabled && ttsPlayer.playing) orb.setState('speaking');
                        }
                    }
                    if (data.search_results) {
                        renderSearchResults(data.search_results);
                        if (searchResultsToggle) searchResultsToggle.style.display = '';
                        if (searchResultsWidget && settings.autoOpenSearchResults) { searchResultsWidget.classList.add('open'); updatePanelOverlay(); }
                    }
                    if (data.actions) {
                        handleActions(data.actions, contentEl);
                    }
                    if ('chunk' in data) {
                        const chunkText = data.chunk || '';
                        if (chunkText && !firstChunkReceived) {
                            firstChunkReceived = true;
                            // Stop starter audio immediately for clean handoff to actual TTS
                            if (preStarterPlayer && preStarterPlayer.audio) {
                                preStarterPlayer.audio.pause();
                                preStarterPlayer.audio.currentTime = 0;
                            }
                            if (ttsPlayer) ttsPlayer.reset();
                        }
                        fullResponse += chunkText;
                        const textSpan = contentEl.querySelector('.msg-stream-text');
                        if (textSpan) {
                            textSpan.textContent = fullResponse;
                            textSpan.classList.remove('stream-placeholder');
                        }
                        if (!cursorEl) {
                            cursorEl = document.createElement('span');
                            cursorEl.className = 'stream-cursor';
                            cursorEl.textContent = '|';
                            contentEl.appendChild(cursorEl);
                        }
                        scrollToBottom();
                    }
                    if (data.audio && ttsPlayer) {
                        ttsPlayer.enqueue(data.audio);
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
        if (cursorEl) cursorEl.remove();
        const textSpan = contentEl.querySelector('.msg-stream-text');
        if (textSpan && !fullResponse) textSpan.textContent = '(No response)';
    } catch (err) {
        clearTimeout(timeoutId);
        removeTypingIndicator();
        if (err.name === 'AbortError') {
            // If PTT triggered the abort, stay silent — user is interrupting to speak
            if (!isRecording) {
                addMessage('assistant', 'Request timed out. Please try again.');
                showToast('Request timed out. Please try again.', 6000);
            }
        } else {
            let msg = 'Something went wrong. Please try again.';
            if (err.message && err.message.includes('503')) {
                msg = 'Service temporarily unavailable. Please try again in a moment.';
            } else if (err.message && err.message.includes('429')) {
                msg = 'Rate limit reached. Please wait a moment before trying again.';
            } else if (err.message && err.message.length > 0) {
                msg = err.message.length > 100 ? err.message.slice(0, 97) + '...' : err.message;
            }
            addMessage('assistant', msg);
            showToast(msg, 6000);
        }
    } finally {
        clearTimeout(timeoutId);
        isStreaming = false;
        pttStreamController = null;
        if (sendBtn) sendBtn.disabled = false;
        if (messageInput) messageInput.disabled = false;
        if (orbContainer) orbContainer.classList.remove('active');
        // Only reset to idle if TTS isn't actively playing
        if (orb && !(ttsPlayer && ttsPlayer.playing)) orb.setState('idle');
    }
}

/* ═══════════════════════════════════════════════════════════════════════
   M8: Reminders & Notes Panel Management
   ═══════════════════════════════════════════════════════════════════════ */

// ── Reminders Panel ──
const remindersPanel = document.getElementById('reminders-panel');
const remindersClose = document.getElementById('reminders-close');
const remindersMinimize = document.getElementById('reminders-minimize');
const remindersList = document.getElementById('reminders-list');
const remindersEmpty = document.getElementById('reminders-empty');
const remindersBtn = document.getElementById('reminders-btn');
const remindersPanelHeader = document.getElementById('reminders-panel-header');

// ── Notes Panel ──
const notesPanel = document.getElementById('notes-panel');
const notesClose = document.getElementById('notes-close');
const notesMinimize = document.getElementById('notes-minimize');
const notesList = document.getElementById('notes-list');
const notesEmpty = document.getElementById('notes-empty');
const todoLists = document.getElementById('todo-lists');
const todoEmpty = document.getElementById('todo-empty');
const notesBtn = document.getElementById('notes-btn');
const notesPanelHeader = document.getElementById('notes-panel-header');
const notesTabs = document.querySelectorAll('.notes-tab');
const notesTabContents = document.querySelectorAll('.notes-tab-content');

// ── Reminder toast container ──
const reminderToastContainer = document.getElementById('reminder-toast-container');

/* ─────────────────────────────────────────
   Generic Panel Drag & Resize
   ───────────────────────────────────────── */
function makeDraggable(panel, header) {
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
        isDragging = false;
        panel.style.transition = '';
        document.removeEventListener('mousemove', onDrag);
        document.removeEventListener('mouseup', stopDrag);
    }
}

function makeResizable(panel, handle) {
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
        isResizing = false;
        panel.style.transition = '';
        document.removeEventListener('mousemove', onResize);
        document.removeEventListener('mouseup', stopResize);
    }
}

// Initialize drag & resize
makeDraggable(remindersPanel, remindersPanelHeader);
makeDraggable(notesPanel, notesPanelHeader);
makeResizable(remindersPanel, document.getElementById('reminders-panel-resize'));
makeResizable(notesPanel, document.getElementById('notes-panel-resize'));


/* ─────────────────────────────────────────
   Reminders Panel Logic
   ───────────────────────────────────────── */
async function fetchReminders(filter = 'all') {
    try {
        const res = await fetch(`${API}/api/reminders?filter=${filter}`);
        if (!res.ok) return [];
        const data = await res.json();
        return data.reminders || [];
    } catch { return []; }
}

function formatReminderTime(isoStr) {
    try {
        const d = new Date(isoStr);
        const now = new Date();
        const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const rDate = new Date(d.getFullYear(), d.getMonth(), d.getDate());
        const diffDays = Math.round((rDate - today) / 86400000);

        let dateStr = '';
        if (diffDays === 0) dateStr = 'Today';
        else if (diffDays === 1) dateStr = 'Tomorrow';
        else if (diffDays === -1) dateStr = 'Yesterday';
        else dateStr = d.toLocaleDateString('en-IN', { month: 'short', day: 'numeric' });

        const timeStr = d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: true });
        return `${dateStr} at ${timeStr}`;
    } catch {
        return isoStr;
    }
}

function renderReminders(reminders) {
    if (!remindersList || !remindersEmpty) return;
    if (!reminders || reminders.length === 0) {
        remindersList.innerHTML = '';
        remindersEmpty.style.display = 'block';
        return;
    }
    remindersEmpty.style.display = 'none';

    // Group by: overdue, today, upcoming
    const now = new Date();
    const todayEnd = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 23, 59, 59);
    const overdue = [], today = [], upcoming = [];
    reminders.forEach(r => {
        const d = new Date(r.due_at);
        if (d < now) overdue.push(r);
        else if (d <= todayEnd) today.push(r);
        else upcoming.push(r);
    });

    let html = '';
    if (overdue.length) {
        html += '<div class="reminder-section-label">⚠️ Overdue</div>';
        overdue.forEach(r => { html += buildReminderCard(r, true); });
    }
    if (today.length) {
        html += '<div class="reminder-section-label">📅 Today</div>';
        today.forEach(r => { html += buildReminderCard(r); });
    }
    if (upcoming.length) {
        html += '<div class="reminder-section-label">🔮 Upcoming</div>';
        upcoming.forEach(r => { html += buildReminderCard(r); });
    }
    remindersList.innerHTML = html;

    // Bind action buttons
    remindersList.querySelectorAll('[data-reminder-action]').forEach(btn => {
        btn.addEventListener('click', async e => {
            const action = btn.dataset.reminderAction;
            const id = parseInt(btn.dataset.reminderId);
            if (action === 'done') {
                await fetch(`${API}/api/reminders/${id}/done`, { method: 'POST' });
                openRemindersPanel(); // refresh
            } else if (action === 'delete') {
                await fetch(`${API}/api/reminders/${id}`, { method: 'DELETE' });
                openRemindersPanel(); // refresh
            } else if (action === 'snooze') {
                await fetch(`${API}/api/reminders/${id}/snooze`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ minutes: 10 })
                });
                openRemindersPanel(); // refresh
            }
        });
    });
}

function buildReminderCard(r, isOverdue = false) {
    const recurrenceBadge = r.recurrence
        ? `<span class="reminder-card-recurrence">🔁 ${r.recurrence}</span>`
        : '';
    const desc = r.description ? `<div style="font-size:0.75rem;color:rgba(255,255,255,0.4);margin-top:2px;">${escHtml(r.description)}</div>` : '';
    return `
        <div class="reminder-card" style="${isOverdue ? 'border-color:rgba(255,107,107,0.25);' : ''}">
            <div class="reminder-card-header">
                <span class="reminder-card-title">${escHtml(r.title)} ${recurrenceBadge}</span>
            </div>
            <div class="reminder-card-time">${formatReminderTime(r.due_at)}</div>
            ${desc}
            <div class="reminder-card-actions">
                <button class="reminder-action-btn" data-reminder-action="done" data-reminder-id="${r.id}">✓ Done</button>
                <button class="reminder-action-btn" data-reminder-action="snooze" data-reminder-id="${r.id}">⏰ Snooze</button>
                <button class="reminder-action-btn danger" data-reminder-action="delete" data-reminder-id="${r.id}">🗑️</button>
            </div>
        </div>`;
}

function escHtml(s) {
    const d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
}

let _remindersPanelTimer = null;
async function openRemindersPanel() {
    if (!remindersPanel) return;
    remindersPanel.setAttribute('aria-hidden', 'false');
    if (remindersBtn) remindersBtn.classList.add('active');
    const reminders = await fetchReminders();
    renderReminders(reminders);
    // Auto-hide after 30 seconds
    if (_remindersPanelTimer) clearTimeout(_remindersPanelTimer);
    _remindersPanelTimer = setTimeout(() => closeRemindersPanel(), 30000);
}

function closeRemindersPanel() {
    if (!remindersPanel) return;
    remindersPanel.setAttribute('aria-hidden', 'true');
    if (remindersBtn) remindersBtn.classList.remove('active');
    if (_remindersPanelTimer) { clearTimeout(_remindersPanelTimer); _remindersPanelTimer = null; }
}

// Toggle on button click
if (remindersBtn) {
    remindersBtn.addEventListener('click', () => {
        const isOpen = remindersPanel && remindersPanel.getAttribute('aria-hidden') === 'false';
        if (isOpen) closeRemindersPanel();
        else openRemindersPanel();
    });
}
if (remindersClose) remindersClose.addEventListener('click', closeRemindersPanel);
if (remindersMinimize) remindersMinimize.addEventListener('click', closeRemindersPanel);


/* ─────────────────────────────────────────
   Notes & To-Do Panel Logic
   ───────────────────────────────────────── */

// Tab switching
notesTabs.forEach(tab => {
    tab.addEventListener('click', () => {
        notesTabs.forEach(t => t.classList.remove('active'));
        notesTabContents.forEach(c => c.classList.remove('active'));
        tab.classList.add('active');
        const tabName = tab.dataset.tab;
        const content = document.getElementById(tabName === 'notes' ? 'notes-content' : 'todo-content');
        if (content) content.classList.add('active');
    });
});

async function fetchNotes(search = null) {
    try {
        let url = `${API}/api/notes`;
        if (search) url += `?search=${encodeURIComponent(search)}`;
        const res = await fetch(url);
        if (!res.ok) return [];
        const data = await res.json();
        return data.notes || [];
    } catch { return []; }
}

async function fetchTodos() {
    try {
        const res = await fetch(`${API}/api/todos`);
        if (!res.ok) return [];
        const data = await res.json();
        return data.lists || [];
    } catch { return []; }
}

function renderNotes(notes) {
    if (!notesList || !notesEmpty) return;
    if (!notes || notes.length === 0) {
        notesList.innerHTML = '';
        notesEmpty.style.display = 'block';
        return;
    }
    notesEmpty.style.display = 'none';
    let html = '';
    notes.forEach(n => {
        const pin = n.pinned ? '<span class="note-card-pin">📌</span>' : '';
        const body = n.markdown_body || n.body || '';
        const preview = body.length > 120 ? body.slice(0, 120) + '...' : body;
        const time = n.updated_at || n.created_at || '';
        let timeStr = '';
        try { timeStr = new Date(time).toLocaleDateString('en-IN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); } catch {}

        html += `
            <div class="note-card" data-note-id="${n.id}">
                <div class="note-card-header">
                    <span class="note-card-title">${pin} ${escHtml(n.title)}</span>
                    <div class="note-card-actions">
                        <button class="note-card-btn delete" data-note-delete="${n.id}" title="Delete">🗑️</button>
                    </div>
                </div>
                <div class="note-card-body">${escHtml(preview)}</div>
                <div class="note-card-time">${timeStr}</div>
            </div>`;
    });
    notesList.innerHTML = html;

    // Bind delete buttons
    notesList.querySelectorAll('[data-note-delete]').forEach(btn => {
        btn.addEventListener('click', async e => {
            e.stopPropagation();
            const id = parseInt(btn.dataset.noteDelete);
            await fetch(`${API}/api/notes/${id}`, { method: 'DELETE' });
            openNotesPanel('notes');
        });
    });

    // Expand/collapse on click
    notesList.querySelectorAll('.note-card-body').forEach(body => {
        body.addEventListener('click', () => {
            body.classList.toggle('expanded');
        });
    });
}

function renderTodos(lists) {
    if (!todoLists || !todoEmpty) return;
    if (!lists || lists.length === 0) {
        todoLists.innerHTML = '';
        todoEmpty.style.display = 'block';
        return;
    }
    todoEmpty.style.display = 'none';
    let html = '';
    lists.forEach(lst => {
        const items = lst.items || [];
        const done = items.filter(i => i.done).length;
        const total = items.length;

        let itemsHtml = '';
        items.forEach(item => {
            const doneClass = item.done ? 'done' : '';
            const checkClass = item.done ? 'checked' : '';
            itemsHtml += `
                <div class="todo-item ${doneClass}" data-item-id="${item.id}">
                    <div class="todo-checkbox ${checkClass}" data-todo-toggle="${item.id}" data-done="${item.done ? 1 : 0}"></div>
                    <span class="todo-item-text">${escHtml(item.text)}</span>
                    <button class="todo-item-delete" data-todo-item-delete="${item.id}">×</button>
                </div>`;
        });

        html += `
            <div class="todo-list-card" data-list-id="${lst.id}">
                <div class="todo-list-header">
                    <span class="todo-list-title">${escHtml(lst.title)}</span>
                    <span class="todo-list-progress">${done}/${total}</span>
                    <div class="todo-list-actions">
                        <button class="note-card-btn delete" data-todo-delete="${lst.id}" title="Delete list">🗑️</button>
                    </div>
                </div>
                ${itemsHtml}
                <div class="todo-add-input">
                    <input type="text" placeholder="Add item..." data-todo-add-input="${lst.id}" />
                    <button class="todo-add-btn" data-todo-add-btn="${lst.id}">+</button>
                </div>
            </div>`;
    });
    todoLists.innerHTML = html;

    // Bind todo checkboxes
    todoLists.querySelectorAll('[data-todo-toggle]').forEach(cb => {
        cb.addEventListener('click', async () => {
            const id = parseInt(cb.dataset.todoToggle);
            const currentDone = cb.dataset.done === '1';
            await fetch(`${API}/api/todos/items/${id}/done`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ done: !currentDone })
            });
            openNotesPanel('todo');
        });
    });

    // Bind delete item
    todoLists.querySelectorAll('[data-todo-item-delete]').forEach(btn => {
        btn.addEventListener('click', async e => {
            e.stopPropagation();
            const id = parseInt(btn.dataset.todoItemDelete);
            await fetch(`${API}/api/todos/items/${id}`, { method: 'DELETE' });
            openNotesPanel('todo');
        });
    });

    // Bind delete list
    todoLists.querySelectorAll('[data-todo-delete]').forEach(btn => {
        btn.addEventListener('click', async e => {
            e.stopPropagation();
            const id = parseInt(btn.dataset.todoDelete);
            await fetch(`${API}/api/todos/${id}`, { method: 'DELETE' });
            openNotesPanel('todo');
        });
    });

    // Bind add item
    todoLists.querySelectorAll('[data-todo-add-btn]').forEach(btn => {
        btn.addEventListener('click', async () => {
            const listId = parseInt(btn.dataset.todoAddBtn);
            const input = todoLists.querySelector(`[data-todo-add-input="${listId}"]`);
            if (!input || !input.value.trim()) return;
            await fetch(`${API}/api/todos/${listId}/items`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ items: [input.value.trim()] })
            });
            input.value = '';
            openNotesPanel('todo');
        });
    });

    // Enter key to add item
    todoLists.querySelectorAll('[data-todo-add-input]').forEach(input => {
        input.addEventListener('keydown', e => {
            if (e.key === 'Enter') {
                const listId = input.dataset.todoAddInput;
                const btn = todoLists.querySelector(`[data-todo-add-btn="${listId}"]`);
                if (btn) btn.click();
            }
        });
    });
}

let _notesPanelTimer = null;
async function openNotesPanel(tab = 'notes') {
    if (!notesPanel) return;
    notesPanel.setAttribute('aria-hidden', 'false');
    if (notesBtn) notesBtn.classList.add('active');

    // Switch to correct tab
    notesTabs.forEach(t => {
        t.classList.toggle('active', t.dataset.tab === tab);
    });
    notesTabContents.forEach(c => c.classList.remove('active'));
    const content = document.getElementById(tab === 'notes' ? 'notes-content' : 'todo-content');
    if (content) content.classList.add('active');

    // Load data
    if (tab === 'notes') {
        const notes = await fetchNotes();
        renderNotes(notes);
    } else {
        const lists = await fetchTodos();
        renderTodos(lists);
    }
    // Auto-hide after 30 seconds
    if (_notesPanelTimer) clearTimeout(_notesPanelTimer);
    _notesPanelTimer = setTimeout(() => closeNotesPanel(), 30000);
}

function closeNotesPanel() {
    if (!notesPanel) return;
    notesPanel.setAttribute('aria-hidden', 'true');
    if (notesBtn) notesBtn.classList.remove('active');
    if (_notesPanelTimer) { clearTimeout(_notesPanelTimer); _notesPanelTimer = null; }
}

// Toggle on button click
if (notesBtn) {
    notesBtn.addEventListener('click', () => {
        const isOpen = notesPanel && notesPanel.getAttribute('aria-hidden') === 'false';
        if (isOpen) closeNotesPanel();
        else openNotesPanel();
    });
}
if (notesClose) notesClose.addEventListener('click', closeNotesPanel);
if (notesMinimize) notesMinimize.addEventListener('click', closeNotesPanel);


/* ─────────────────────────────────────────
   SSE Notification Stream (Reminders)
   ───────────────────────────────────────── */
let notifEventSource = null;
let notifLastId = 0;

function connectNotificationStream() {
    if (notifEventSource) {
        try { notifEventSource.close(); } catch {}
    }
    const url = `${API}/api/notifications/stream`;
    notifEventSource = new EventSource(url);

    notifEventSource.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.type === 'reminder') {
                showReminderToast(data);
                playNotificationSound();
                // Refresh reminders panel if open
                if (remindersPanel && remindersPanel.getAttribute('aria-hidden') === 'false') {
                    openRemindersPanel();
                }
            }
            notifLastId = data.event_id || notifLastId;
        } catch {}
    };

    notifEventSource.onerror = () => {
        // Reconnect after 5 seconds
        try { notifEventSource.close(); } catch {}
        setTimeout(connectNotificationStream, 5000);
    };
}

// Start SSE connection after page loads
setTimeout(connectNotificationStream, 2000);


/* ─────────────────────────────────────────
   Reminder Toast Notifications
   ───────────────────────────────────────── */
function showReminderToast(data) {
    if (!reminderToastContainer) return;
    const toast = document.createElement('div');
    toast.className = 'reminder-toast';
    const desc = data.description ? `<div class="reminder-toast-desc">${escHtml(data.description)}</div>` : '';
    toast.innerHTML = `
        <div class="reminder-toast-header">
            <span class="reminder-toast-icon">🔔</span>
            <span class="reminder-toast-title">${escHtml(data.title)}</span>
        </div>
        ${desc}
        <div class="reminder-toast-actions">
            <button class="reminder-toast-btn primary" data-toast-action="done">✓ Done</button>
            <button class="reminder-toast-btn" data-toast-action="snooze">⏰ Snooze 10m</button>
            <button class="reminder-toast-btn" data-toast-action="dismiss">Dismiss</button>
        </div>`;

    reminderToastContainer.appendChild(toast);

    // Auto dismiss after 30 seconds
    const autoTimer = setTimeout(() => removeToast(toast), 30000);

    // Bind toast actions
    toast.querySelectorAll('[data-toast-action]').forEach(btn => {
        btn.addEventListener('click', async e => {
            e.stopPropagation();
            clearTimeout(autoTimer);
            const action = btn.dataset.toastAction;
            if (action === 'done' && data.id) {
                await fetch(`${API}/api/reminders/${data.id}/done`, { method: 'POST' });
            } else if (action === 'snooze' && data.id) {
                await fetch(`${API}/api/reminders/${data.id}/snooze`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ minutes: 10 })
                });
            }
            removeToast(toast);
            if (remindersPanel && remindersPanel.getAttribute('aria-hidden') === 'false') {
                openRemindersPanel();
            }
        });
    });

    // Also request browser notification permission and show a native notification
    if ('Notification' in window && Notification.permission === 'granted') {
        new Notification('J.A.R.V.I.S Reminder', {
            body: data.title + (data.description ? '\n' + data.description : ''),
            icon: '/web/favicon.ico',
            tag: 'reminder-' + data.id,
        });
    } else if ('Notification' in window && Notification.permission !== 'denied') {
        Notification.requestPermission();
    }
}

function removeToast(toast) {
    toast.classList.add('removing');
    setTimeout(() => toast.remove(), 300);
}


/* ─────────────────────────────────────────
   Notification Sound (Web Audio API)
   ───────────────────────────────────────── */
let notifAudioCtx = null;

function playNotificationSound() {
    try {
        if (!notifAudioCtx) notifAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
        const ctx = notifAudioCtx;

        // Pleasant two-tone chime
        const now = ctx.currentTime;
        const osc1 = ctx.createOscillator();
        const osc2 = ctx.createOscillator();
        const gain1 = ctx.createGain();
        const gain2 = ctx.createGain();

        osc1.type = 'sine';
        osc1.frequency.value = 880; // A5
        gain1.gain.setValueAtTime(0.3, now);
        gain1.gain.exponentialRampToValueAtTime(0.01, now + 0.3);
        osc1.connect(gain1);
        gain1.connect(ctx.destination);
        osc1.start(now);
        osc1.stop(now + 0.3);

        osc2.type = 'sine';
        osc2.frequency.value = 1320; // E6
        gain2.gain.setValueAtTime(0.25, now + 0.15);
        gain2.gain.exponentialRampToValueAtTime(0.01, now + 0.5);
        osc2.connect(gain2);
        gain2.connect(ctx.destination);
        osc2.start(now + 0.15);
        osc2.stop(now + 0.5);
    } catch {}
}

document.addEventListener('DOMContentLoaded', init);

/* ===================== Conversation History ===================== */
const HISTORY_SEARCH_DEBOUNCE_MS = 280;

/* --- URL routing -------------------------------------------------------
 * The URL is the single source of truth for which conversation is open:
 *   /jarvis/            -> a fresh chat (no id yet)
 *   /jarvis/c/<id>      -> that conversation
 * A new chat gets its id only once the first message comes back from the
 * server, at which point the URL is *replaced* (not pushed) so Back does not
 * land on an empty draft. Same behaviour as ChatGPT.
 * The app is also mounted at /app, so the base is derived, not hardcoded. */
const CHAT_BASE_PATH  = location.pathname.startsWith('/app') ? '/app/' : '/jarvis/';
const CHAT_URL_PREFIX = CHAT_BASE_PATH + 'c/';

function sessionIdFromUrl() {
    if (!location.pathname.startsWith(CHAT_URL_PREFIX)) return null;
    const raw = location.pathname.slice(CHAT_URL_PREFIX.length).replace(/\/+$/, '');
    if (!raw) return null;
    try { return decodeURIComponent(raw); } catch (_) { return raw; }
}

/** mode: 'replace' (default) | 'push' | 'none'. */
function syncUrl(id, mode) {
    if (mode === 'none') return;
    const path = id ? CHAT_URL_PREFIX + encodeURIComponent(id) : CHAT_BASE_PATH;
    if (location.pathname === path) return;
    const entry = { sessionId: id || null };
    try {
        if (mode === 'push') history.pushState(entry, '', path);
        else history.replaceState(entry, '', path);
    } catch (_) { /* history API unavailable -- the app still works */ }
}

const historyPanel        = $('history-panel');
const historyToggle       = $('history-toggle');
const historyClose        = $('history-close');
const historyOverlay      = $('history-overlay');
const historyList         = $('history-list');
const historyNewBtn       = $('history-new-btn');
const historySearchInput  = $('history-search-input');
const historySearchClear  = $('history-search-clear');
const historyLoading      = $('history-loading');
const historyEmpty        = $('history-empty');
const historyNoResults    = $('history-no-results');
const historyError        = $('history-error');
const historyRetry        = $('history-retry');
const historyLoadMore     = $('history-load-more');
const historyStatus       = $('history-status');
const historyDialogBackdrop = $('history-dialog-backdrop');
const historyRenameDialog = $('history-rename-dialog');
const historyRenameInput  = $('history-rename-input');
const historyRenameCancel = $('history-rename-cancel');
const historyRenameSave   = $('history-rename-save');
const historyDeleteDialog = $('history-delete-dialog');
const historyDeleteName   = $('history-delete-name');
const historyDeleteCancel = $('history-delete-cancel');
const historyDeleteConfirm = $('history-delete-confirm');

const historyState = {
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

/** Single place where the active session id changes, so the URL and the sidebar
 *  highlight can never drift from the streaming session.
 *  urlMode: 'replace' (default -- a draft becoming a real conversation),
 *           'push' (user navigated to another conversation),
 *           'none' (we got here *from* the URL, e.g. Back/Forward). */
function setActiveSession(id, urlMode = 'replace') {
    const changed = sessionId !== id;
    sessionId = id;
    syncUrl(id, urlMode);
    if (changed && id) {
        markActiveHistoryItem();
        // A brand new conversation has no summary yet; refresh so it appears.
        if (!historyState.items.some(it => it.session_id === id)) {
            loadHistory({ silent: true });
        }
    }
}

function announceHistory(message) {
    if (historyStatus) historyStatus.textContent = message || '';
}

function isHistoryOpen() {
    return !!(historyPanel && historyPanel.classList.contains('open'));
}

function openHistoryPanel() {
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

function closeHistoryPanel() {
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

function toggleHistoryPanel() {
    if (isHistoryOpen()) closeHistoryPanel(); else openHistoryPanel();
}

function setHistoryStateView({ loading, empty, noResults, error }) {
    if (historyLoading)   historyLoading.hidden   = !loading;
    if (historyEmpty)     historyEmpty.hidden     = !empty;
    if (historyNoResults) historyNoResults.hidden = !noResults;
    if (historyError)     historyError.hidden     = !error;
}

async function loadHistory({ append = false, silent = false } = {}) {
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
function historyGroupFor(isoString) {
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

function historyRelativeTime(isoString) {
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

const HISTORY_GROUP_ORDER = ['Today', 'Yesterday', 'Previous 7 Days', 'Previous 30 Days', 'Older'];

function renderHistory() {
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
function buildHistoryItem(item) {
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

function closeHistoryMenu() {
    historyState.openMenuId = null;
    document.querySelectorAll('.history-item-menu').forEach(m => m.remove());
    document.querySelectorAll('.history-item.menu-open').forEach(r => r.classList.remove('menu-open'));
}

function toggleHistoryMenu(item, row) {
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

function markActiveHistoryItem() {
    if (!historyList) return;
    historyList.querySelectorAll('.history-item').forEach(row => {
        row.classList.toggle('active', row.dataset.sessionId === sessionId);
    });
}

/** opts.urlMode -- 'push' (default) for a user click, 'none' when we are
 *  reacting to the URL itself (initial load, Back/Forward).
 *  Resolves true when the conversation was opened. */
async function selectConversation(id, opts = {}) {
    const urlMode = opts.urlMode || 'push';
    if (!id || historyState.switching) return false;
    if (id === sessionId) { closeHistoryPanelOnMobile(); return true; }
    if (isStreaming) {
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

        if (isRecording) pttStopRecording();
        if (ttsPlayer) ttsPlayer.stop();
        stopStartupBrief();

        chatMessages.innerHTML = '';
        (data.messages || []).forEach(m => addMessage(m.role === 'assistant' ? 'assistant' : 'user', m.content || ''));
        if (!(data.messages || []).length) chatMessages.appendChild(createWelcome());

        // Activity/search panels belong to the current turn, not to a reopened
        // transcript -- reset rather than show stale telemetry.
        resetTurnPanels();
        setActiveSession(id, urlMode);
        markActiveHistoryItem();
        scrollToBottom();
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
function resetTurnPanels() {
    if (searchResultsWidget) searchResultsWidget.classList.remove('open');
    if (searchResultsToggle) searchResultsToggle.style.display = 'none';
    if (activityPanel) activityPanel.classList.remove('open');
    if (activityList) {
        activityList.innerHTML = '<div class="activity-empty" id="activity-empty">Send a message to see the flow here.</div>';
    }
    if (activityToggle) activityToggle.style.display = 'none';
    updatePanelOverlay();
}

function closeHistoryPanelOnMobile() {
    if (window.matchMedia('(max-width: 768px)').matches) closeHistoryPanel();
}

/* ── Rename ── */
function openRenameDialog(item) {
    if (!historyRenameDialog || !historyDialogBackdrop) return;
    historyState.dialogSessionId = item.session_id;
    historyState.lastFocus = document.activeElement;
    historyRenameInput.value = item.title || '';
    historyDialogBackdrop.hidden = false;
    historyRenameDialog.hidden = false;
    if (historyDeleteDialog) historyDeleteDialog.hidden = true;
    historyRenameInput.focus();
    historyRenameInput.select();
}

function closeHistoryDialogs() {
    if (historyDialogBackdrop) historyDialogBackdrop.hidden = true;
    if (historyRenameDialog) historyRenameDialog.hidden = true;
    if (historyDeleteDialog) historyDeleteDialog.hidden = true;
    historyState.dialogSessionId = null;
    if (historyState.lastFocus && typeof historyState.lastFocus.focus === 'function') {
        historyState.lastFocus.focus();
    }
}

async function submitRename() {
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
function openDeleteDialog(item) {
    if (!historyDeleteDialog || !historyDialogBackdrop) return;
    historyState.dialogSessionId = item.session_id;
    historyState.lastFocus = document.activeElement;
    if (historyDeleteName) historyDeleteName.textContent = item.title || 'This conversation';
    historyDialogBackdrop.hidden = false;
    historyDeleteDialog.hidden = false;
    if (historyRenameDialog) historyRenameDialog.hidden = true;
    if (historyDeleteConfirm) historyDeleteConfirm.focus();
}

async function submitDelete() {
    const id = historyState.dialogSessionId;
    if (!id) return;
    if (id === sessionId && isStreaming) {
        showToast('Jarvis is still replying in this conversation.');
        return;
    }
    if (historyDeleteConfirm) historyDeleteConfirm.disabled = true;

    try {
        const res = await fetch(`${API}/chat/history/${encodeURIComponent(id)}`, { method: 'DELETE' });
        if (!res.ok && res.status !== 404) throw new Error(`HTTP ${res.status}`);
        historyState.items = historyState.items.filter(it => it.session_id !== id);
        const wasActive = id === sessionId;
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
function openSessionFromUrl() {
    const id = sessionIdFromUrl();
    if (!id) return;
    selectConversation(id, { urlMode: 'none' });
}

/** Back/Forward. The URL has already changed by the time this fires, so the
 *  handler only brings the app in line with it -- never writes history back. */
function onHistoryPopState() {
    const id = sessionIdFromUrl();
    if (id === sessionId) return;
    if (isStreaming) {
        // Cannot swap conversations mid-reply; put the address bar back.
        showToast('Jarvis is still replying — wait for it to finish.');
        syncUrl(sessionId, 'replace');
        return;
    }
    if (!id) { newChat({ urlMode: 'none' }); return; }
    selectConversation(id, { urlMode: 'none' });
}

function initHistory() {
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
