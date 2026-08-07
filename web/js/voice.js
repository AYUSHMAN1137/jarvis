/* ---------------------------------------------------------------------------
 * voice.js
 *
 * Speech: TTS playback, the startup brief, and push to talk.
 *
 * Extracted from the single 4000-line script.js by /data/p9_3b.py.
 * [M14 P9.3]
 * ------------------------------------------------------------------------- */

import { reset } from './bus.js';
import { messageInput, sendMessage } from './chat.js';
import { API, PRE_STARTER_PHRASES } from './config.js';
import { $ } from './dom.js';
import { orbContainer } from './orbctl.js';
import { showToast } from './panels.js';
import { state } from './state.js';
/* ── Push-to-Talk (Ctrl+Shift) state ── */

export let currentTranscript = '';
export let finalReceived = false;
export let pttSafetyTimer = null;
export let ctrlHeld = false;
export let shiftHeld = false;
export let pttSendDone = false;
  // 'web-speech' | 'whisper' | null
export let recognition = null;

export const micBtn       = $('mic-btn');
export const ttsBtn       = $('tts-btn');

export class PreStarterPlayer {
    constructor() {
        this.audio = document.createElement('audio');
        this.audio.preload = 'auto';
    }
    play(onComplete) {
        const loaded = PRE_STARTER_PHRASES.filter(p => state.PRE_STARTER_CACHE[p]);
        if (loaded.length === 0) {
            if (onComplete) onComplete();
            return;
        }
        const phrase = loaded[Math.floor(Math.random() * loaded.length)];
        const base64 = state.PRE_STARTER_CACHE[phrase];
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



export class TTSPlayer {
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
        if (state.orb) state.orb.setState('idle');

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
        if (state.orb) state.orb.setState('speaking');

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
        if (state.orb) state.orb.setState('idle');

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

export let startupBriefPlayed = false;
export let startupBriefController = null;
export let startupBriefDone = false;

/** Immediately stop the daily startup greeting (text stream + TTS audio) when
 *  the user interrupts by sending a message or starting to speak. Once stopped
 *  it will NOT resume. */
export function stopStartupBrief() {
    startupBriefDone = true;
    if (startupBriefController) {
        try { startupBriefController.abort(); } catch (_) {}
        startupBriefController = null;
    }
    if (state.ttsPlayer && state.ttsPlayer.playing) {
        state.ttsPlayer.stop();
        state.ttsPlayer.stopped = false;
    }
}

export function playStartupBrief() {
    if (startupBriefPlayed) return;
    startupBriefPlayed = true;
    startupBriefDone = false;
    startupBriefController = new AbortController();

    // Attach unlock to first interaction to bypass autoplay restrictions
    const unlockAndPlay = () => {
        if (state.ttsPlayer && state.ttsPlayer.unlock) {
            state.ttsPlayer.unlock();
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
                                if (!startupBriefDone && data.audio && state.ttsPlayer) {
                                    state.ttsPlayer.enqueue(data.audio);
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


/* ══════════════════════════════════════════════════════════════════
   Push-to-Talk  (Ctrl+Shift hold → record → release → send)
   HYBRID: MediaRecorder (reliable) + Web Speech API (live preview)
   ══════════════════════════════════════════════════════════════════ */

/** Shared AbortController so PTT can abort an in-flight stream */

export let mediaRecorder = null;
export let audioChunks = [];
export let micStream = null;

export function initPushToTalk() {
    // ── Keyboard event listeners ──
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Control') ctrlHeld = true;
        if (e.key === 'Shift') shiftHeld = true;

        if (ctrlHeld && shiftHeld && !state.isRecording) {
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

        if ((!ctrlHeld || !shiftHeld) && state.isRecording) {
            pttStopRecording();
        }
    });

    window.addEventListener('blur', () => {
        ctrlHeld = false;
        shiftHeld = false;
        if (state.isRecording) pttStopRecording();
    });

    if (micBtn) micBtn.title = 'Voice input — hold Ctrl+Shift to speak';
    console.log('[PTT] Hybrid Push-to-Talk initialized (MediaRecorder + Web Speech API)');
}

/** Start BOTH MediaRecorder and Web Speech API in parallel */
export async function pttStartRecording() {
    if (state.isRecording) return;

    // ── Interrupt the daily startup greeting if it's playing ──
    stopStartupBrief();

    // ── Interrupt streaming if active ──
    if (state.isStreaming && state.pttStreamController) {
        console.log('[PTT] Interrupting active stream...');
        state.pttStreamController.abort();
    }

    // ── Interrupt TTS if playing ──
    if (state.ttsPlayer && state.ttsPlayer.playing) {
        state.ttsPlayer.stop();
        state.ttsPlayer.stopped = false;
    }
    // Stop pre-starter audio
    if (state.preStarterPlayer && state.preStarterPlayer.audio && !state.preStarterPlayer.audio.paused) {
        state.preStarterPlayer.audio.pause();
        state.preStarterPlayer.audio.currentTime = 0;
    }

    state.isRecording = true;
    if (state.orb) state.orb.setState('listening');
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
        state.isRecording = false;
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
                    if (!state.isRecording && !pttSendDone) {
                        pttSendTranscript();
                    }
                }
                // Live preview
                if (messageInput && !pttSendDone) messageInput.value = currentTranscript;
            };
            recognition.onerror = () => {}; // silent — MediaRecorder is the backup
            recognition.onend = () => {
                if (!pttSendDone && currentTranscript.trim() && !state.isRecording) {
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

export function pttStopRecording() {
    if (!state.isRecording) return;
    state.isRecording = false;
    pttUpdateUI(false);
    if (state.orb) state.orb.setState('thinking');

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
export function pttSendTranscript() {
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
    state.pttVoiceSource = 'web-speech';
    sendMessage(text);
}

/** FALLBACK PATH: Send recorded audio blob to backend for Whisper transcription */
export async function pttSendAudioToBackend() {
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
        state.pttVoiceSource = 'whisper';
        sendMessage(text);
    } catch (err) {
        console.error('[PTT] Backend transcription failed:', err);
        showToast('Transcription failed. Please try again.');
        if (messageInput) messageInput.placeholder = 'Message Jarvis...';
    }
}

export function pttUpdateUI(active) {
    if (!micBtn) return;
    if (active) {
        micBtn.classList.add('listening');
    } else {
        micBtn.classList.remove('listening');
    }
}
