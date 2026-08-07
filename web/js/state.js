/* ---------------------------------------------------------------------------
 * state.js
 *
 * Mutable state more than one module touches.
 *
 * Extracted from the single 4000-line script.js by /data/p9_3b.py.
 * [M14 P9.3]
 * ------------------------------------------------------------------------- */

/* No imports on purpose. config.js imports this module, so importing config
 * back created a cycle: whichever side was evaluated second read the other's
 * bindings while they were still in their temporal dead zone, and the app died
 * with "Cannot access 'DEFAULT_SETTINGS' before initialization".
 *
 * The defaults are still defined exactly once, in config.js. It seeds them
 * here as its own first act - see the Object.assign at the top of that file.
 *   [M14 P9.3] */

/* These were top-level `let`s in script.js, which only worked because every
 * file shared one global scope. An imported binding is read-only, so
 * `sessionId = id` from another module would throw under ES modules. One
 * object keeps the writes legal and makes the shared surface countable:
 * anything not in here is private to its module, and should stay that way.
 *   [M14 P9.1 / P9.3] */
export const state = {
    // phrase -> base64 audio, filled lazily
    PRE_STARTER_CACHE: {},
    // push to talk is capturing
    isRecording: false,
    // how the current text arrived, for the reply path
    pttVoiceSource: null,
    // the single TTS player
    ttsPlayer: null,
    // canned pre-roll audio player
    preStarterPlayer: null,
    // aborts the in-flight push to talk upload
    pttStreamController: null,
    // live MediaStream while the camera panel is open
    camStream: null,
    // the OrbRenderer instance, or null if WebGL failed
    orb: null,
    // orb dashboard global sliders
    orbGlobals: { lerpRate: 6, baseHue: 0, orbSize: 600, idleOpacity: 0.35 },
    // current conversation id; history and chat both write it
    sessionId: null,
    // a turn is in flight
    isStreaming: false,
    // User settings, mirrored to localStorage. Seeded from DEFAULT_SETTINGS
    // by config.js at import time, then overlaid with whatever
    // loadSettings() finds in storage.
    settings: {},
    // active mode pill
    currentMode: 'jarvis',
};
