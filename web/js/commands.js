/* ---------------------------------------------------------------------------
 * commands.js - the command registry and the Ctrl/Cmd+K palette.
 *
 * Two things live here, on purpose:
 *
 *   1. A registry. Every action reachable from the palette is declared the
 *      same way, in one list. The shortcuts overlay reads that same list, so
 *      the help cannot drift from what the app can actually do - the usual
 *      failure of a hand-written cheat sheet.
 *
 *   2. The palette UI that searches it.
 *
 * This module imports nothing from the feature modules. Commands are
 * registered from main.js by passing a run() closure, which keeps this a leaf
 * of the import graph: no cycles, and the palette still works if a feature
 * module fails.
 *
 * Matching is a subsequence scorer, not a fuzzy-search library. "nc" should
 * find "New chat" and "th" should find "Toggle history"; that is the whole
 * requirement, and it does not justify a dependency.
 *
 * Accessibility: the input is a combobox, the results are a listbox, and the
 * highlighted row is pointed at by aria-activedescendant. Focus never moves
 * into the list - that is what lets you keep typing while arrowing through
 * results - and it returns to whatever had it when the palette closes.
 *   [M14 P10.2]
 * ------------------------------------------------------------------------- */

import { svgIcon } from './icons.js';
const registry = new Map();

/**
 * Declare a command.
 *
 * @param {object} cmd
 * @param {string} cmd.id            stable identity, e.g. 'chat.new'
 * @param {string} cmd.title         what the user reads
 * @param {string} [cmd.group]       section heading in the palette
 * @param {string[]} [cmd.keywords]  extra words that should match
 * @param {string} [cmd.shortcut]    display form, e.g. 'Ctrl+K'
 * @param {Function} cmd.run         the action
 * @param {Function} [cmd.when]      availability predicate; default: always
 */
export function registerCommand(cmd) {
    if (!cmd || !cmd.id || typeof cmd.run !== 'function') {
        console.warn('[commands] ignoring malformed command', cmd);
        return;
    }
    if (registry.has(cmd.id)) {
        // Two registrations under one id means one of them silently never runs.
        console.warn('[commands] duplicate id "' + cmd.id + '" - keeping the first');
        return;
    }
    registry.set(cmd.id, {
        group: 'General',
        keywords: [],
        shortcut: '',
        when: () => true,
        ...cmd,
    });
}

/** Every registered command, in registration order. */
export function allCommands() {
    return [...registry.values()];
}

/** Commands whose when() currently says yes. A throwing predicate hides its
 *  command rather than taking the palette down with it. */
export function availableCommands() {
    return allCommands().filter((c) => {
        try {
            return c.when() !== false;
        } catch (e) {
            console.warn('[commands] when() threw for "' + c.id + '"', e);
            return false;
        }
    });
}

/* ---------------------------------------------------------------- matching */

/**
 * Subsequence score. Returns -1 when the query is not a subsequence of the
 * text, otherwise a number where higher is better.
 *
 * The bonuses are what make it feel right rather than merely correct:
 * consecutive characters beat scattered ones, and a character starting a word
 * beats one buried mid-word.
 */
export function scoreMatch(query, text) {
    const q = String(query || '').trim().toLowerCase();
    if (!q) return 0;
    const t = String(text || '').toLowerCase();
    let ti = 0;
    let score = 0;
    let streak = 0;
    for (const ch of q) {
        if (ch === ' ') { streak = 0; continue; }
        const found = t.indexOf(ch, ti);
        if (found === -1) return -1;
        const startsWord = found === 0 || /[\s\-_/:.]/.test(t[found - 1]);
        score += 10;
        if (startsWord) score += 15;
        if (found === ti) { streak += 1; score += 8 * streak; } else { streak = 0; }
        score -= Math.min(found - ti, 10);      // distance costs, but is capped
        ti = found + 1;
    }
    return score;
}

/** Rank available commands against a query. An empty query keeps declared order. */
export function searchCommands(query) {
    const cmds = availableCommands();
    if (!String(query || '').trim()) return cmds;
    return cmds
        .map((c) => {
            const hay = [c.title, c.group, ...(c.keywords || [])].join(' ');
            // A title hit outranks a hit that only landed in the keywords.
            const direct = scoreMatch(query, c.title);
            const loose = scoreMatch(query, hay);
            return { c, s: Math.max(direct >= 0 ? direct + 20 : -1, loose) };
        })
        .filter((r) => r.s >= 0)
        .sort((a, b) => b.s - a.s)
        .map((r) => r.c);
}

/* ------------------------------------------------------------------ palette */

let root = null;
let input = null;
let list = null;
let results = [];
let cursor = 0;
let lastFocused = null;

export function isPaletteOpen() {
    return !!root && !root.hidden;
}

function build() {
    if (root) return;
    root = document.createElement('div');
    root.className = 'cmdk-backdrop';
    root.hidden = true;

    const box = document.createElement('div');
    box.className = 'cmdk';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-label', 'Command palette');

    const row = document.createElement('div');
    row.className = 'cmdk-input-row';
    row.appendChild(svgIcon('search', { class: 'cmdk-input-icon' }));

    input = document.createElement('input');
    input.className = 'cmdk-input';
    input.type = 'text';
    input.id = 'cmdk-input';
    input.autocomplete = 'off';
    input.spellcheck = false;
    input.placeholder = 'Search commands';
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-expanded', 'true');
    input.setAttribute('aria-controls', 'cmdk-list');
    input.setAttribute('aria-autocomplete', 'list');
    row.appendChild(input);

    list = document.createElement('ul');
    list.className = 'cmdk-list';
    list.id = 'cmdk-list';
    list.setAttribute('role', 'listbox');
    list.setAttribute('aria-label', 'Commands');

    const hint = document.createElement('div');
    hint.className = 'cmdk-hint';
    hint.textContent = 'Enter to run  -  Esc to close';

    box.appendChild(row);
    box.appendChild(list);
    box.appendChild(hint);
    root.appendChild(box);
    document.body.appendChild(root);

    input.addEventListener('input', () => renderResults(input.value));
    input.addEventListener('keydown', onInputKey);
    // Clicking the dimmed area closes; clicking inside must not.
    root.addEventListener('mousedown', (e) => { if (e.target === root) closePalette(); });
}

function rowFor(cmd, index) {
    const li = document.createElement('li');
    li.className = 'cmdk-item';
    li.id = 'cmdk-item-' + index;
    li.setAttribute('role', 'option');
    li.setAttribute('aria-selected', String(index === cursor));

    const label = document.createElement('span');
    label.className = 'cmdk-item-title';
    label.textContent = cmd.title;

    const group = document.createElement('span');
    group.className = 'cmdk-item-group';
    group.textContent = cmd.group;

    li.appendChild(label);
    li.appendChild(group);
    if (cmd.shortcut) {
        const kbd = document.createElement('kbd');
        kbd.className = 'cmdk-item-key';
        kbd.textContent = cmd.shortcut;
        li.appendChild(kbd);
    }

    // mousedown, not click: click would first blur the input and fight the
    // focus restore on close.
    li.addEventListener('mousedown', (e) => { e.preventDefault(); runAt(index); });
    li.addEventListener('mousemove', () => setCursor(index));
    return li;
}

function renderResults(query) {
    results = searchCommands(query || '');
    cursor = 0;
    list.replaceChildren();
    if (!results.length) {
        const li = document.createElement('li');
        li.className = 'cmdk-empty';
        li.textContent = 'No matching commands';
        list.appendChild(li);
        input.removeAttribute('aria-activedescendant');
        return;
    }
    results.forEach((c, i) => list.appendChild(rowFor(c, i)));
    syncCursor();
}

function syncCursor() {
    const items = list.querySelectorAll('.cmdk-item');
    items.forEach((li, i) => li.setAttribute('aria-selected', String(i === cursor)));
    const active = items[cursor];
    if (active) {
        input.setAttribute('aria-activedescendant', active.id);
        // block:'nearest' keeps the list from jumping when the row is visible.
        active.scrollIntoView({ block: 'nearest' });
    } else {
        input.removeAttribute('aria-activedescendant');
    }
}

function setCursor(i) {
    if (!results.length) return;
    cursor = (i + results.length) % results.length;
    syncCursor();
}

function runAt(i) {
    const cmd = results[i];
    if (!cmd) return;
    closePalette();
    try {
        cmd.run();
    } catch (e) {
        console.error('[commands] "' + cmd.id + '" failed', e);
    }
}

function onInputKey(e) {
    if (e.key === 'ArrowDown') { e.preventDefault(); setCursor(cursor + 1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setCursor(cursor - 1); }
    else if (e.key === 'Home') { e.preventDefault(); setCursor(0); }
    else if (e.key === 'End') { e.preventDefault(); setCursor(results.length - 1); }
    else if (e.key === 'Enter') { e.preventDefault(); runAt(cursor); }
    else if (e.key === 'Tab') { e.preventDefault(); setCursor(cursor + (e.shiftKey ? -1 : 1)); }
    else if (e.key === 'Escape') {
        // Stop here. Escape also stops a running turn, and closing the palette
        // must not cancel the reply the user is reading behind it.
        e.preventDefault();
        e.stopPropagation();
        closePalette();
    }
}

export function openPalette() {
    build();
    if (isPaletteOpen()) return;
    lastFocused = document.activeElement;
    root.hidden = false;
    document.body.classList.add('cmdk-open');
    input.value = '';
    renderResults('');
    input.focus();
}

export function closePalette() {
    if (!isPaletteOpen()) return;
    root.hidden = true;
    document.body.classList.remove('cmdk-open');
    results = [];
    // Give focus back to whatever the user was doing, but never to something
    // that has since left the document.
    if (lastFocused && document.contains(lastFocused) && typeof lastFocused.focus === 'function') {
        lastFocused.focus();
    }
    lastFocused = null;
}

export function togglePalette() {
    if (isPaletteOpen()) closePalette();
    else openPalette();
}

/**
 * Bind the global shortcut.
 *
 * Ctrl/Cmd+K only. Push to talk owns Ctrl+Shift, so the Shift and Alt
 * modifiers are explicitly rejected rather than ignored - otherwise holding
 * the talk key and brushing K would open a dialog mid-sentence.
 */
export function initCommandPalette() {
    document.addEventListener('keydown', (e) => {
        const k = (e.key || '').toLowerCase();
        if (k !== 'k' || e.shiftKey || e.altKey) return;
        if (!(e.ctrlKey || e.metaKey)) return;
        e.preventDefault();
        togglePalette();
    }, true);
}
