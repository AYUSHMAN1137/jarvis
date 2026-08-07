/* ---------------------------------------------------------------------------
 * api.js
 *
 * Backend base URL health and the starter-audio warmup.
 *
 * Extracted from the single 4000-line script.js by /data/p9_3b.py.
 * [M14 P9.3]
 * ------------------------------------------------------------------------- */

import { API, PRE_STARTER_PHRASES } from './config.js';
import { statusDot, statusText } from './orbctl.js';
import { state } from './state.js';
/* Starter audio.  [M14 P7.5]
   Was: 10 sequential POST /tts on every page load, before the user had typed a
   character. Server-cached and therefore cheap for the server, but still 10
   round trips and 10 base64 blobs resident in memory, competing for CPU on a
   cold start with the embedding model.

   Now: fetch ONE up front so the first realtime query still has instant audio,
   and the rest on first idle -- and nothing at all if TTS is switched off. */
export let _starterPreloadStarted = false;

/** Fetches one phrase through the normal /tts endpoint = the ONE voice cache.
 *  First run: miss -> synthesized + saved on the server. After that: instant hit. */
export async function fetchStarter(phrase) {
    const base = (typeof window !== 'undefined' && window.location.origin) ? window.location.origin : '';
    try {
        const r = await fetch(`${base}/tts`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: phrase }),
        });
        if (!r.ok) return;
        const blob = await r.blob();
        const base64 = await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onloadend = () => resolve((reader.result || '').split(',')[1] || '');
            reader.onerror = reject;
            reader.readAsDataURL(blob);
        });
        if (base64) state.PRE_STARTER_CACHE[phrase] = base64;
    } catch (_) {}
}

export async function preloadStarterAudio() {
    // Idempotent: the TTS toggle also calls this, and flipping it twice must
    // not fetch the set twice.
    if (_starterPreloadStarted) return;
    if (!(state.ttsPlayer && state.ttsPlayer.enabled)) return;   // TTS off -> fetch nothing at all
    _starterPreloadStarted = true;
    await fetchStarter(PRE_STARTER_PHRASES[0]);
    const rest = () => {
        for (let i = 1; i < PRE_STARTER_PHRASES.length; i++) fetchStarter(PRE_STARTER_PHRASES[i]);
    };
    if ('requestIdleCallback' in window) requestIdleCallback(rest, { timeout: 8000 });
    else setTimeout(rest, 4000);
}


export async function checkHealth() {
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
            const currentOrbState = state.orb ? state.orb.stateName : 'idle';
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
