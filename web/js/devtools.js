/* ═══════════════════════════════════════════════════════════════════
   Dev-only perf overlay.  M14 P0.
   ───────────────────────────────────────────────────────────────────
   Inert by default.  Enable with either:
     • ?perf=1 in the URL
     • localStorage.setItem('jarvis_perf', '1')
   Disable with localStorage.removeItem('jarvis_perf') and drop the query param.

   Why this exists: every performance claim in IMPLEMENTATION_PLAN_UI_M14.md
   has to be checkable without opening DevTools, so regressions get noticed
   during normal use instead of six weeks later.

   Why it is opt-in rather than always-on: an always-on rAF loop is exactly
   the kind of idle cost P2 exists to remove.  This file must never add a
   frame of work unless someone asked for it.
   ═══════════════════════════════════════════════════════════════════ */

const PERF_KEY = 'jarvis_perf';

function perfEnabled() {
    try {
        if (new URLSearchParams(location.search).get('perf') === '1') return true;
        return localStorage.getItem(PERF_KEY) === '1';
    } catch (_) {
        return false;   // private mode / storage disabled — stay off
    }
}

export function initDevtools() {
    if (!perfEnabled()) return;

    const el = document.createElement('div');
    el.id = 'perf-overlay';
    el.setAttribute('aria-hidden', 'true');   // decorative; never announced
    // Inline styles on purpose: this must not depend on style.css, so it keeps
    // working while style.css is being refactored.
    el.style.cssText = [
        'position:fixed', 'bottom:8px', 'left:8px', 'z-index:99999',
        'font:11px/1.45 ui-monospace,SFMono-Regular,Consolas,monospace',
        'color:#9ef', 'background:rgba(0,0,0,0.72)', 'padding:6px 9px',
        'border-radius:6px', 'pointer-events:none', 'white-space:pre',
        'border:1px solid rgba(255,255,255,0.12)',
    ].join(';');
    document.body.appendChild(el);

    let frames = 0;
    let worst = 0;
    let last = performance.now();
    let windowStart = last;
    let longTasks = 0;

    // PerformanceObserver for long tasks — the metric that actually correlates
    // with "the UI felt stuck", unlike average FPS.
    try {
        new PerformanceObserver((list) => { longTasks += list.getEntries().length; })
            .observe({ entryTypes: ['longtask'] });
    } catch (_) { /* Firefox has no longtask observer — degrade quietly */ }

    const tick = (now) => {
        const dt = now - last;
        last = now;
        frames++;
        if (dt > worst) worst = dt;

        if (now - windowStart >= 1000) {
            const fps = Math.round((frames * 1000) / (now - windowStart));
            const mem = performance.memory
                ? `\nheap  ${(performance.memory.usedJSHeapSize / 1048576).toFixed(1)} MB`
                : '';
            el.textContent =
                `fps   ${fps}\n` +
                `worst ${worst.toFixed(1)} ms\n` +
                `long  ${longTasks}` +
                mem +
                `\ndpr   ${window.devicePixelRatio}`;
            frames = 0;
            worst = 0;
            windowStart = now;
        }
        requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);

    console.info('[perf] overlay on. localStorage.removeItem("jarvis_perf") to disable.');
}
