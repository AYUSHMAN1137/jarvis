
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
        if (orb) orb.setActive(false);

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
        if (orb) orb.setActive(true);

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
        if (orb) orb.setActive(false);

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
    initPushToTalk();
    preloadStarterAudio();
    preStarterPlayer = new PreStarterPlayer();
    checkHealth();
    startBackgroundActivityPolling();
    playStartupBrief();
    bindEvents();
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
                }).catch(err => console.error("Error reading startup stream:", err));
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

    } catch (_) {}
}

function saveSettings() {
    try {
        localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
    } catch (_) {}
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
                    if (data.session_id) sessionId = data.session_id;
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
        if (statusText) statusText.textContent = ok ? 'Online' : 'Offline';
    } catch (e) {
        if (statusDot) statusDot.classList.add('offline');
        if (statusText) statusText.textContent = 'Offline';
        if (typeof console !== 'undefined' && console.warn) console.warn('[Health] Check failed:', e);
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

}

function autoResizeInput() {
    messageInput.style.height = 'auto';
    messageInput.style.height = Math.min(messageInput.scrollHeight, 120) + 'px';
}

function updatePanelOverlay() {
    if (!panelOverlay) return;
    const anyOpen = (activityPanel && activityPanel.classList.contains('open')) ||
        (searchResultsWidget && searchResultsWidget.classList.contains('open')) ||
        (settingsPanel && settingsPanel.classList.contains('open'));
    panelOverlay.classList.toggle('visible', !!anyOpen);
}

function setMode(mode) {
    currentMode = mode || 'jarvis';
    if (btnJarvis) btnJarvis.classList.add('active');
    if (modeSlider) modeSlider.classList.remove('center', 'right');
    if (activityToggle) activityToggle.style.display = '';
}

function newChat() {
    if (isRecording) pttStopRecording();
    if (ttsPlayer) ttsPlayer.stop();
    if (camStream) stopCamera();
    sessionId = null;
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
    decision:              { step: 2, label: 'Brain analysis' },
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
    cache_status:          { step: 0, label: 'Cache update' },
    fast_path:             { step: 0, label: 'Direct fast path' },
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
        const cat = (activity.query_type || '?').charAt(0).toUpperCase() + (activity.query_type || '').slice(1);
        const t = fmtTime(activity.elapsed_ms);
        detail = t ? `${cat} (${t})` : cat;
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
    } else if (activity.event === 'fast_path') {
        detail = `${activity.tool || 'tool'} selected without an LLM call`;
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
                        verdict: row.verdict, reason: row.reason, source: row.source }
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
                    if (data.session_id) sessionId = data.session_id;
                    if (data.activity) {
                        appendActivity(data.activity);
                        if (activityToggle) activityToggle.style.display = '';
                        if (activityPanel && settings.autoOpenActivity) { activityPanel.classList.add('open'); updatePanelOverlay(); }
                        // Play starter audio ONLY when brain decides this is a realtime (web search) query
                        if (data.activity.event === 'decision' && data.activity.query_type === 'realtime') {
                            if (ttsPlayer?.enabled && settings.thinkingSounds && preStarterPlayer) {
                                preStarterPlayer.play(() => {
                                });
                            }
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
    }
}

document.addEventListener('DOMContentLoaded', init);
