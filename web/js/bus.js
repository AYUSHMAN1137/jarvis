/* ---------------------------------------------------------------------------
 * bus.js - a deliberately small publish / subscribe channel.
 *
 * Modules that must not import each other still need to notice each other:
 * the activity panel cares that a turn started, the orb cares that a verdict
 * failed. Wiring that with direct imports either creates a cycle or forces one
 * module to know about five others.
 *
 * The event names are a frozen table, not free strings. An open bus grows a
 * second, untraceable control flow within a month, and nobody can answer "what
 * can happen in this app" without grepping. Six events. Adding a seventh is a
 * decision, and it happens here.
 *   [M14 P9.4]
 * ------------------------------------------------------------------------- */

export const EVENTS = Object.freeze({
    TURN_START: 'turn:start',
    TURN_END: 'turn:end',
    ACTIVITY_ROW: 'activity:row',
    VERDICT_FAIL: 'verdict:fail',
    SESSION_CHANGE: 'session:change',
    ORB_STATE: 'orb:state',
});

const NAMES = new Set(Object.values(EVENTS));
const listeners = new Map();

function check(name) {
    if (!NAMES.has(name)) {
        // Loud, but not fatal: a typo should be obvious in the console without
        // taking a turn down with it.
        console.warn('[bus] unknown event "' + name + '" - see EVENTS in js/bus.js');
        return false;
    }
    return true;
}

/** Subscribe. Returns an unsubscribe function. */
export function on(name, fn) {
    if (!check(name)) return () => {};
    if (!listeners.has(name)) listeners.set(name, new Set());
    listeners.get(name).add(fn);
    return () => { const s = listeners.get(name); if (s) s.delete(fn); };
}

/** Publish. One throwing listener must never stop the others, or block the
 *  caller that emitted mid-turn. */
export function emit(name, payload) {
    if (!check(name)) return;
    const set = listeners.get(name);
    if (!set) return;
    for (const fn of [...set]) {
        try { fn(payload); } catch (e) { console.warn('[bus] listener for ' + name + ' threw', e); }
    }
}

/** Test / teardown helper. */
export function reset() {
    listeners.clear();
}
