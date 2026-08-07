/* ---------------------------------------------------------------------------
 * shortcuts.js - the "?" keyboard reference.
 *
 * The list is generated from the command registry, not typed out by hand. A
 * hand-written cheat sheet is wrong within a month: someone adds a command and
 * forgets the help, or renames one and leaves the old wording behind. Reading
 * the registry means the sheet is a view of the truth.
 *
 * Four entries cannot come from the registry, because they are not commands -
 * they are raw key handling in voice.js and chat.js (hold to talk, send,
 * newline, stop). Those are declared once in MANUAL below, next to a note
 * saying where the real handler lives, so the next person can check them.
 *   [M14 P10.3]
 * ------------------------------------------------------------------------- */

import { allCommands, availableCommands } from './commands.js';
/* Keys handled directly by their own modules. Keep in step with:
 *   voice.js - Ctrl+Shift hold, push to talk
 *   chat.js  - Enter / Shift+Enter, and the Escape funnel */
const MANUAL = [
    { group: 'Voice', title: 'Hold to talk', keys: ['Ctrl', 'Shift'] },
    { group: 'Chat', title: 'Send message', keys: ['Enter'] },
    { group: 'Chat', title: 'New line in the message box', keys: ['Shift', 'Enter'] },
    { group: 'Chat', title: 'Stop generating, or close a panel', keys: ['Esc'] },
    { group: 'General', title: 'Command palette', keys: ['Ctrl', 'K'] },
    { group: 'General', title: 'This help', keys: ['?'] },
];

let root = null;
let lastFocused = null;

export function isShortcutsOpen() {
    return !!root && !root.hidden;
}

function keyRow(title, keys) {
    const row = document.createElement('div');
    row.className = 'shortcuts-row';

    const label = document.createElement('span');
    label.textContent = title;

    const box = document.createElement('span');
    box.className = 'shortcuts-keys';
    for (const k of keys) {
        const kbd = document.createElement('kbd');
        kbd.textContent = k;
        box.appendChild(kbd);
    }

    row.appendChild(label);
    row.appendChild(box);
    return row;
}

/**
 * Rows to display: the manual keys, plus every registered command that
 * declares a shortcut. Commands without one are still reachable - the palette
 * line at the top says how - so listing them here would only be noise.
 */
export function shortcutRows() {
    const rows = MANUAL.map((m) => ({ ...m }));
    for (const c of allCommands()) {
        if (!c.shortcut) continue;
        if (rows.some((r) => r.title === c.title)) continue;
        rows.push({ group: c.group, title: c.title, keys: c.shortcut.split('+') });
    }
    return rows;
}

function build() {
    if (root) return;
    root = document.createElement('div');
    root.className = 'cmdk-backdrop';        // same dimmed modal shell
    root.hidden = true;

    const sheet = document.createElement('div');
    sheet.className = 'shortcuts-sheet';
    sheet.setAttribute('role', 'dialog');
    sheet.setAttribute('aria-modal', 'true');
    sheet.setAttribute('aria-labelledby', 'shortcuts-title');
    sheet.tabIndex = -1;

    const h = document.createElement('h2');
    h.className = 'shortcuts-title';
    h.id = 'shortcuts-title';
    h.textContent = 'Keyboard shortcuts';

    const sub = document.createElement('p');
    sub.className = 'shortcuts-sub';
    sub.textContent = 'Press Ctrl+K for the command palette. Esc closes this.';

    sheet.appendChild(h);
    sheet.appendChild(sub);

    const rows = shortcutRows();
    const groups = [];
    for (const r of rows) {
        let g = groups.find((x) => x.name === r.group);
        if (!g) { g = { name: r.group, rows: [] }; groups.push(g); }
        g.rows.push(r);
    }
    for (const g of groups) {
        const head = document.createElement('h3');
        head.className = 'shortcuts-group-name';
        head.textContent = g.name;
        sheet.appendChild(head);
        for (const r of g.rows) sheet.appendChild(keyRow(r.title, r.keys));
    }

    // Commands without a key still deserve a mention, as a count rather than a
    // second copy of the palette.
    const n = availableCommands().length;
    const foot = document.createElement('p');
    foot.className = 'shortcuts-sub';
    foot.style.marginTop = '18px';
    foot.textContent = n + ' commands are available in the palette.';
    sheet.appendChild(foot);

    root.appendChild(sheet);
    document.body.appendChild(root);
    root.addEventListener('mousedown', (e) => { if (e.target === root) closeShortcuts(); });
    sheet.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            // Escape also stops a running turn; swallow it here.
            e.preventDefault();
            e.stopPropagation();
            closeShortcuts();
        }
    });
}

export function openShortcuts() {
    // Rebuilt on every open: the registry can gain commands after startup, and
    // a stale sheet is exactly the failure this module exists to prevent.
    if (root) { root.remove(); root = null; }
    build();
    lastFocused = document.activeElement;
    root.hidden = false;
    document.body.classList.add('cmdk-open');
    root.querySelector('.shortcuts-sheet').focus();
}

export function closeShortcuts() {
    if (!isShortcutsOpen()) return;
    root.hidden = true;
    document.body.classList.remove('cmdk-open');
    if (lastFocused && document.contains(lastFocused) && typeof lastFocused.focus === 'function') {
        lastFocused.focus();
    }
    lastFocused = null;
}

export function toggleShortcuts() {
    if (isShortcutsOpen()) closeShortcuts();
    else openShortcuts();
}

/** True when the key event came from somewhere the user is writing text. */
function isTyping(target) {
    if (!target) return false;
    const tag = (target.tagName || '').toLowerCase();
    return tag === 'input' || tag === 'textarea' || target.isContentEditable === true;
}

/**
 * Bind "?" and F1.
 *
 * "?" is a printable character, so it must never fire while the user is typing
 * - that would eat the question mark out of their sentence. F1 is bound too
 * because it is the platform habit and it works from anywhere.
 */
export function initShortcutsOverlay() {
    document.addEventListener('keydown', (e) => {
        if (e.ctrlKey || e.metaKey || e.altKey) return;
        const isHelp = e.key === 'F1' || e.key === '?';
        if (!isHelp) return;
        if (e.key === '?' && isTyping(e.target)) return;
        e.preventDefault();
        toggleShortcuts();
    });
}
