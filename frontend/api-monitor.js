const summaryEl = document.getElementById('monitor-summary');
const gridEl = document.getElementById('monitor-grid');
const eventsEl = document.getElementById('monitor-events-list');
const refreshBtn = document.getElementById('monitor-refresh');
const statusText = document.getElementById('monitor-status-text');
const statusDot = document.querySelector('#monitor-status .status-dot');

const API = window.location.origin;
let pollingTimer = null;

function fmtTime(ts) {
    if (!ts) return '—';
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return '—';
    return d.toLocaleTimeString();
}

function pct(success, attempts) {
    if (!attempts) return 0;
    return Math.max(0, Math.min(100, Math.round((success / attempts) * 100)));
}

function cardForKey(k, latestKeyLabels) {
    const successPct = pct(k.successes, k.attempts);
    const isLatest = latestKeyLabels && latestKeyLabels.has(k.key_label);
    return `
        <article class="monitor-key-card glass-panel${isLatest ? ' latest-call' : ''}">
            <div class="monitor-key-top">
                <div class="monitor-key-name">${k.key_label}</div>
                <div class="monitor-key-mask">${k.key_masked}</div>
            </div>
            <div class="monitor-progress">
                <div class="monitor-progress-bar" style="width:${successPct}%"></div>
            </div>
            <div class="monitor-key-stats">
                <span>Attempts: <b>${k.attempts}</b></span>
                <span>Success: <b>${k.successes}</b></span>
                <span>Fail: <b>${k.failures}</b></span>
                <span>429: <b>${k.rate_limits}</b></span>
                <span>In-flight: <b>${k.in_flight}</b></span>
                <span>Last ok: <b>${fmtTime(k.last_success_at)}</b></span>
            </div>
            ${k.last_error ? `<div class="monitor-key-error">Last error: ${k.last_error}</div>` : ''}
        </article>
    `;
}

function eventItem(e, isLatest) {
    const cls = e.event === 'success' ? 'evt-success' : e.event === 'failure' ? 'evt-failure' : 'evt-attempt';
    const detail = [
        e.provider?.toUpperCase(),
        e.key_label || '',
        e.operation || '',
        e.source || '',
        e.rate_limited ? 'rate_limited' : '',
    ].filter(Boolean).join(' • ');
    const error = e.error ? `<div class="monitor-evt-error">${e.error}</div>` : '';
    return `
        <div class="monitor-evt ${cls}${isLatest ? ' latest-call' : ''}">
            <div class="monitor-evt-head">
                <span class="monitor-evt-badge">${e.event}</span>
                <span class="monitor-evt-time">${fmtTime(e.timestamp)}</span>
            </div>
            <div class="monitor-evt-detail">${detail || 'event'}</div>
            ${error}
        </div>
    `;
}

function render(data) {
    const groq = data?.groq || { summary: {}, keys: [], configured_keys: 0 };
    const summary = groq.summary || {};
    const tavily = data?.providers?.tavily || { configured: false, attempts: 0, successes: 0, failures: 0 };
    const successPct = pct(summary.successes || 0, summary.attempts || 0);
    const latestEvent = data?.events?.[0] || null;
    const latestTraceId = latestEvent?.trace_id || null;
    const latestTraceEvents = latestTraceId
        ? (data?.events || []).filter(e => e?.trace_id === latestTraceId)
        : [];
    const latestKeyLabels = new Set(
        latestTraceEvents.map(e => e?.key_label).filter(Boolean)
    );
    if (latestKeyLabels.size === 0 && latestEvent?.key_label) {
        latestKeyLabels.add(latestEvent.key_label);
    }

    summaryEl.innerHTML = `
        <div class="monitor-summary-grid">
            <div class="monitor-stat"><span>Configured Groq keys</span><b>${groq.configured_keys || 0}</b></div>
            <div class="monitor-stat"><span>Attempts</span><b>${summary.attempts || 0}</b></div>
            <div class="monitor-stat"><span>Success</span><b>${summary.successes || 0}</b></div>
            <div class="monitor-stat"><span>Failures</span><b>${summary.failures || 0}</b></div>
            <div class="monitor-stat"><span>Rate limits</span><b>${summary.rate_limits || 0}</b></div>
            <div class="monitor-stat"><span>Success rate</span><b>${successPct}%</b></div>
            <div class="monitor-stat"><span>Tavily configured</span><b>${tavily.configured ? 'Yes' : 'No'}</b></div>
            <div class="monitor-stat"><span>Tavily usage</span><b>${tavily.attempts || 0}</b></div>
        </div>
    `;

    if (!groq.keys || groq.keys.length === 0) {
        gridEl.innerHTML = '<div class="activity-empty glass-panel">No Groq keys configured.</div>';
    } else {
        gridEl.innerHTML = groq.keys.map(k => cardForKey(k, latestKeyLabels)).join('');
    }

    if (!data?.events || data.events.length === 0) {
        eventsEl.innerHTML = '<div class="activity-empty">No events yet. Send a message in Jarvis.</div>';
    } else {
        eventsEl.innerHTML = data.events.slice(0, 60).map((e, i) => {
            const isLatest = latestTraceId ? e?.trace_id === latestTraceId : i === 0;
            return eventItem(e, isLatest);
        }).join('');
    }
}

async function refresh() {
    try {
        const r = await fetch(`${API}/api/key-monitor`, { cache: 'no-store' });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        render(data);
        statusText.textContent = 'Live';
        if (statusDot) statusDot.classList.remove('offline');
    } catch (e) {
        statusText.textContent = 'Offline';
        if (statusDot) statusDot.classList.add('offline');
    }
}

function startPolling() {
    refresh();
    pollingTimer = setInterval(refresh, 2000);
}

if (refreshBtn) refreshBtn.addEventListener('click', refresh);
window.addEventListener('beforeunload', () => { if (pollingTimer) clearInterval(pollingTimer); });
startPolling();
