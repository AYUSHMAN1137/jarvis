/* ---------------------------------------------------------------------------
 * notifications.js
 *
 * Reminder SSE stream and reminder toasts.
 *
 * Extracted from the single 4000-line script.js by /data/p9_3b.py.
 * [M14 P9.3]
 * ------------------------------------------------------------------------- */

import { API, NOTIF_RETRY_MAX } from './config.js';
import { el } from './dom.js';
import { svgIcon } from './icons.js';
import { openRemindersPanel, remindersPanel } from './reminders.js';
// ── Reminder toast container ──
export const reminderToastContainer = document.getElementById('reminder-toast-container');


/* ─────────────────────────────────────────
   SSE Notification Stream (Reminders)
   ───────────────────────────────────────── */
/* Reminder notification stream.  [M14 P7.3]
   Was: onerror did `setTimeout(connectNotificationStream, 5000)` and stored no
   handle. EventSource.onerror fires repeatedly against a flapping server, so
   every error scheduled ANOTHER reconnect -- overlapping timers, each opening
   another EventSource. A single server restart could leave several live
   streams pushing duplicate reminder toasts. Fixed 5s delay, no backoff, no
   cap.

   Now: at most one stream and at most one pending reconnect, with exponential
   backoff from 1s to 30s that resets the moment a connection succeeds. */
export let notifEventSource = null;
export let notifReconnectTimer = null;
export let notifRetryDelay = 1000;

// Removed notifLastId (M14 P7.4): written, never read. Resuming a missed
// notification needs a `since` param on the SSE endpoint and a decision about
// reminders that fire with no browser open -- out of scope for a UI milestone,
// and half-built state is worse than none.

export function connectNotificationStream() {
    if (notifReconnectTimer) { clearTimeout(notifReconnectTimer); notifReconnectTimer = null; }
    if (notifEventSource) { try { notifEventSource.close(); } catch (_) {} notifEventSource = null; }

    const es = new EventSource(`${API}/api/notifications/stream`);
    notifEventSource = es;

    es.onopen = () => { notifRetryDelay = 1000; };   // a good connection resets the backoff

    es.onmessage = (event) => {
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
        } catch (_) {}
    };

    es.onerror = () => {
        // Errors from a stream we have already replaced are not our problem.
        if (notifEventSource !== es) return;
        try { es.close(); } catch (_) {}
        notifEventSource = null;
        if (notifReconnectTimer) return;   // one pending reconnect, ever
        notifReconnectTimer = setTimeout(() => {
            notifReconnectTimer = null;
            connectNotificationStream();
        }, notifRetryDelay);
        notifRetryDelay = Math.min(notifRetryDelay * 2, NOTIF_RETRY_MAX);
    };
}

/** [M14 P7.3] Recovers a DEAD stream when the tab comes back. This is the
 *  opposite of the activity poller and getting it backwards silently breaks
 *  reminders: the whole point of this stream is firing while the user is doing
 *  something else, so a hidden tab must keep its connection open. */
export function notificationVisibilityChanged(hidden) {
    if (hidden) return;
    if (!notifEventSource && !notifReconnectTimer) connectNotificationStream();
}


/* ─────────────────────────────────────────
   Reminder Toast Notifications
   ───────────────────────────────────────── */
export function showReminderToast(data) {
    if (!reminderToastContainer) return;
    const toast = document.createElement('div');
    toast.className = 'reminder-toast';
    toast.appendChild(el('div', { class: 'reminder-toast-header' }, [
        svgIcon('bell', { class: 'reminder-toast-icon' }),
        el('span', { class: 'reminder-toast-title', text: String(data.title == null ? '' : data.title) })
    ]));
    if (data.description) {
        toast.appendChild(el('div', { class: 'reminder-toast-desc', text: String(data.description) }));
    }
    toast.appendChild(el('div', { class: 'reminder-toast-actions' }, [
        el('button', { type: 'button', class: 'reminder-toast-btn primary', dataset: { toastAction: 'done' } }, [svgIcon('check'), ' Done']),
        el('button', { type: 'button', class: 'reminder-toast-btn', dataset: { toastAction: 'snooze' } }, [svgIcon('clock'), ' Snooze 10m']),
        el('button', { type: 'button', class: 'reminder-toast-btn', dataset: { toastAction: 'dismiss' }, text: 'Dismiss' })
    ]));

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
            // [M14 P12.5] Was '/web/favicon.ico' -- a path that never existed.
            // The app is mounted at /jarvis, not /web, and there was no .ico
            // file anywhere, so every native reminder notification has been
            // showing the browser's default icon. Resolved against <base href>
            // so it keeps working from a /jarvis/c/<id> deep link.
            icon: new URL('favicon.svg', document.baseURI).href,
            tag: 'reminder-' + data.id,
        });
    } else if ('Notification' in window && Notification.permission !== 'denied') {
        Notification.requestPermission();
    }
}

export function removeToast(toast) {
    toast.classList.add('removing');
    setTimeout(() => toast.remove(), 300);
}


/* ─────────────────────────────────────────
   Notification Sound (Web Audio API)
   ───────────────────────────────────────── */
export let notifAudioCtx = null;

export function playNotificationSound() {
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


/* Event wiring that used to sit at the top level of script.js. Under ES
 * modules it cannot stay there: it touches elements owned by other
 * modules, and in an import cycle those bindings are still in their
 * temporal dead zone while this module is being evaluated. main.js calls
 * this once every module exists, in the original source order.
 *   [M14 P9.3] */
export function initWiring() {
    // Start SSE connection after page loads
    setTimeout(connectNotificationStream, 2000);
}
