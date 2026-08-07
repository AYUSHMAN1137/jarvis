/* ---------------------------------------------------------------------------
 * reminders.js
 *
 * Reminders panel.
 *
 * Extracted from the single 4000-line script.js by /data/p9_3b.py.
 * [M14 P9.3]
 * ------------------------------------------------------------------------- */

import { API, PANEL_AUTO_CLOSE_MS } from './config.js';
import { el } from './dom.js';
import { svgIcon } from './icons.js';
import { armAutoClose } from './panels.js';
/* ═══════════════════════════════════════════════════════════════════════
   M8: Reminders & Notes Panel Management
   ═══════════════════════════════════════════════════════════════════════ */

// ── Reminders Panel ──
export const remindersPanel = document.getElementById('reminders-panel');
export const remindersClose = document.getElementById('reminders-close');
export const remindersMinimize = document.getElementById('reminders-minimize');
export const remindersList = document.getElementById('reminders-list');
export const remindersEmpty = document.getElementById('reminders-empty');
export const remindersBtn = document.getElementById('reminders-btn');
export const remindersPanelHeader = document.getElementById('reminders-panel-header');


/* ─────────────────────────────────────────
   Reminders Panel Logic
   ───────────────────────────────────────── */
export async function fetchReminders(filter = 'all') {
    try {
        const res = await fetch(`${API}/api/reminders?filter=${filter}`);
        if (!res.ok) return [];
        const data = await res.json();
        return data.reminders || [];
    } catch { return []; }
}

export function formatReminderTime(isoStr) {
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

export function renderReminders(reminders) {
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

    const frag = document.createDocumentFragment();
    const section = (glyph, label, rows, overdueFlag) => {
        if (!rows.length) return;
        const head = el('div', { class: 'reminder-section-label' });
        head.appendChild(svgIcon(glyph));
        head.appendChild(document.createTextNode(' ' + label));
        frag.appendChild(head);
        rows.forEach(r => frag.appendChild(buildReminderCard(r, overdueFlag)));
    };
    section('warn', 'Overdue', overdue, true);
    section('calendar', 'Today', today, false);
    section('horizon', 'Upcoming', upcoming, false);
    remindersList.replaceChildren(frag);

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

// M14 P4: returns a real element, not an HTML string. Every reminder field
// (title, description, recurrence, formatted time) arrives from the API and is
// therefore untrusted; recurrence in particular used to be interpolated raw,
// which was a working stored-XSS path.
export function buildReminderCard(r, isOverdue = false) {
    const title = el('span', { class: 'reminder-card-title', text: String(r.title == null ? '' : r.title) });
    if (r.recurrence) {
        title.appendChild(document.createTextNode(' '));
        const badge = el('span', { class: 'reminder-card-recurrence' });
        badge.appendChild(svgIcon('repeat'));
        badge.appendChild(document.createTextNode(' ' + String(r.recurrence)));
        title.appendChild(badge);
    }

    const card = el('div', { class: 'reminder-card' }, [
        el('div', { class: 'reminder-card-header' }, [title]),
        el('div', { class: 'reminder-card-time', text: formatReminderTime(r.due_at) })
    ]);
    if (isOverdue) card.style.borderColor = 'rgba(255,107,107,0.25)';

    if (r.description) {
        card.appendChild(el('div', { class: 'reminder-card-desc', text: String(r.description) }));
    }

    const id = String(r.id);
    const mkBtn = (action, glyph, label, danger) => {
        const b = el('button', {
            type: 'button',
            class: 'reminder-action-btn' + (danger ? ' danger' : ''),
            dataset: { reminderAction: action, reminderId: id },
            'aria-label': label
        });
        b.appendChild(svgIcon(glyph));
        if (label) { b.appendChild(document.createTextNode(' ' + label)); }
        return b;
    };
    card.appendChild(el('div', { class: 'reminder-card-actions' }, [
        mkBtn('done', 'check', 'Done', false),
        mkBtn('snooze', 'clock', 'Snooze', false),
        mkBtn('delete', 'trash', '', true)
    ]));
    const del = card.querySelector('[data-reminder-action="delete"]');
    if (del) del.setAttribute('aria-label', 'Delete reminder');
    return card;
}

/* Holds a CANCEL FUNCTION now, not a timer id.  [M14 P10.6] */
export let _remindersPanelTimer = null;
export async function openRemindersPanel(opts = {}) {
    if (!remindersPanel) return;
    remindersPanel.setAttribute('aria-hidden', 'false');
    if (remindersBtn) remindersBtn.classList.add('active');
    const reminders = await fetchReminders();
    renderReminders(reminders);
    /* The blanket 30-second auto-close is gone: it shut the panel while the
       user was reading it. Only a panel the AGENT opened closes itself, and
       even then the timer is cancelled the moment the user touches it.
         [M14 P10.6] */
    if (_remindersPanelTimer) { _remindersPanelTimer(); _remindersPanelTimer = null; }
    if (opts.auto) {
        _remindersPanelTimer = armAutoClose(remindersPanel, PANEL_AUTO_CLOSE_MS, closeRemindersPanel);
    }
}

export function closeRemindersPanel() {
    if (!remindersPanel) return;
    remindersPanel.setAttribute('aria-hidden', 'true');
    if (remindersBtn) remindersBtn.classList.remove('active');
    if (_remindersPanelTimer) { _remindersPanelTimer(); _remindersPanelTimer = null; }
}


/* Event wiring that used to sit at the top level of script.js. Under ES
 * modules it cannot stay there: it touches elements owned by other
 * modules, and in an import cycle those bindings are still in their
 * temporal dead zone while this module is being evaluated. main.js calls
 * this once every module exists, in the original source order.
 *   [M14 P9.3] */
export function initWiring() {
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
}
