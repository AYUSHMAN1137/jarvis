# IMPLEMENTATION PLAN — M14: Frontend UI / UX / Performance

> **Audience:** the AI agent that will implement this. Read §0 → §3 before touching a single file.
> **Scope:** `web/` (chat app, viewer, api-monitor) + `app/static/` (admin dashboards).
> **Out of scope:** everything in `app/services/`, `app/api/`, `config.py` — **except** the four
> small, explicitly-listed backend additions in P7.3 and P12.4. Do not touch `IMPLEMENTATION_PLAN_M13.md` work.
> **Supersedes:** `frontend_ui_analysis.md` (that document was a survey; several of its claims are
> corrected in §1 below). Keep the old file for reference; do not delete it.
>
> **Author's note on sequencing:** the phases are ordered so that every phase leaves the app in a
> shippable state, and so that the cheap high-impact work lands before the expensive refactors.
> **Do not reorder. Do not skip P0.** P1 and P2 alone deliver ~70% of the perceived win.

---

## 0. HOW TO USE THIS DOCUMENT

1. Work **one phase at a time**. Each phase has: `Goal` → `Why` → `Files` → `Steps` → `Verify` → `Rollback`.
2. After **every** phase, run the verification block. If it fails, fix before moving on.
3. After every phase, `git add -A && git commit -m "M14 Pxx: <phase name>"`. One commit per phase.
   This is the rollback mechanism — there is no other one.
4. Each step that changes behaviour has a `⚠ TRAP` note where I know something is easy to break.
   Read the traps. They are all things I verified in the code, not guesses.
5. If a step turns out to be wrong because the code has moved since this plan was written,
   **stop and report** rather than improvising a different design. Line numbers will drift as
   you edit; always re-locate by searching for the quoted code, never by trusting a line number
   alone.

### Line numbers in this document

All line numbers are from the working tree at the time of writing:
- `web/script.js` — **3583 lines**
- `web/style.css` — **4470 lines**
- `web/index.html` — **474 lines**
- `web/orb.js` — **459 lines**

They are given as anchors. **Always confirm by searching for the quoted snippet.** Line numbers
inside a phase are pre-phase values unless stated otherwise.

---

## 1. CORRECTIONS TO `frontend_ui_analysis.md`

The previous survey was directionally right but incomplete or wrong on these points. This plan
assumes the corrected facts.

| Previous claim | Corrected fact |
|---|---|
| "`script.js` ~3k lines" | **3583 lines.** |
| "No visible error recovery on chat stream failure (need to verify)" | **Verified: there is none, and it is actively broken.** A mid-stream network drop appends a *second* assistant bubble under the truncated one with the raw browser string `"Failed to fetch"`, leaves the blinking `.stream-cursor` in the DOM **forever** (the `cursorEl.remove()` at script.js:2342 is on the happy path only, not in `catch`/`finally`), and leaves queued TTS audio playing over a dead stream. There is no retry, no resume, and **no user-facing stop button**. See P3. |
| "Toast container has no max count / queue" | Correct, and worse: `.reminder-toast-container` is `z-index: 9999` and `.jarvis-panel` is `1100`, both **above** `.history-dialog-backdrop` (40). A reminder toast or the notes panel paints over a modal dialog. See P8.3. |
| "`style.css` 4471 lines — fine loaded once" | **Not fine. Lines 2430–3331 (~900 lines, 20% of the file) are a stale duplicate paste of 1290–2063 plus both media queries.** I diffed them rule-by-rule: 87 rules, all byte-equivalent, missing 10 later-added rules. It causes no visual bug today, but **every rule in 1290–2063 is currently un-editable** — a change there is silently reverted by the copy below it. This is the single highest-value fix in the whole plan. See P1. |
| "Star-field costs GPU on low-end devices. Could respect `prefers-reduced-motion`" | Correct but understated. There is **exactly one** `prefers-reduced-motion` block (4466–4470) and it covers **only the history drawer**. **22 of 23 infinite animations ignore the preference**, as do 71 of 76 transitions. See P2.1. |
| "`.btn-icon` size appears ~32×32; WCAG target ≥ 24×24 OK" | Misleading. WCAG 2.2 SC 2.5.8 (Target Size Minimum, AA) is 24×24 **CSS px**, so 32×32 passes — but there are **8 icon buttons** in a 64px header with no wrapping strategy, and at the 480px breakpoint they shrink further. The real problem is header density, not target size. See P10.1. |
| Nothing said about XSS | **There is a real XSS hole.** `buildReminderCard()` (script.js:2566) interpolates `r.recurrence` **raw** into `innerHTML`, and every `id` across the reminders/notes/todos renderers is interpolated raw into `data-*` attributes. Reminders are created **by the LLM from user speech**, so `r.recurrence` is an LLM-controlled string reaching `innerHTML` unescaped. Also `handleActions` (script.js:1202) does `img.src = url` with no scheme check while the sibling link path at 1164 *does* allowlist. See P4. |
| "Emojis used as icons alongside SVG icons — inconsistent" | Correct, and it's 100% confined to the M8 panels (reminders/notes/todos) and the `.jarvis-panel-icon`. Cheap to fix. See P10.4. |
| Nothing said about markdown | **There is no markdown rendering at all.** Assistant replies go to `textContent` (script.js:2319), so the LLM's `**bold**`, `` `code` ``, headings, lists and code fences render as literal syntax. This is the biggest single UX gap in the app. See P6. |
| "Cache-bust versions by hand" | Correct, and currently **only `style.css` has a `?v=`** (index.html:16). `script.js` and `orb.js` have **none** — they are cached with no bust at all. See P12.5. |

### Additional facts the survey missed, all verified

- **`web/` has no subfolders.** 7 flat files: `index.html`, `style.css`, `script.js`, `orb.js`, `viewer.html`, `api-monitor.html`, `api-monitor.js`. Any new folder is a new decision — P9 creates `web/js/` and `web/css/`, nothing else.
- **`sendMessage` (2169–2376, 208 lines) and `sendMessageWithImage` (1366–1461, 96 lines) are ~90% duplicate.** Two copies of the SSE loop. Every fix in P3 and P6 would otherwise have to be written twice. **Merge them first** (P5) — no, actually merge them in P5 *after* P3 lands in one place; see the note in P5.
- **`captureFrameAsBase64()` (script.js:1301) is dead code** — zero call sites. `captureFrameAsBase64Safe()` (1319) is the live one.
- **`notifLastId` (script.js:2862)** is written at 2882, never read. Dead.
- **`@keyframes dotBounce`** (style.css:2041 and 2956) is never referenced. Dead in both copies.
- **`--orb-idle-opacity`** is consumed at style.css:145 but **never declared in CSS** — only ever set by JS (`orb.js` `applyGlobals`). The `0.35` fallback carries it. Fine, but document it.
- **`.speech-widget`, `.speech-widget-text`, `.speech-widget-label`, `.new-chat-btn`, `.mode-btn-text`** are styled **only inside media queries** with no base rule. Either the base rules were lost or the elements no longer exist. **Investigate before deleting** (P1.4).
- **`.send-btn` carries 5 `!important`s** (style.css:1168–1177) that exist only to beat `.action-btn` (1142) — which has identical specificity and loses on source order anyway. All 5 are removable.
- **Zero `will-change`, zero `contain`, zero `content-visibility`** in the entire stylesheet. Every panel open/close allocates and tears down a compositor layer.
- **The activity polling loop (script.js:2061) never stops.** `window.setInterval(poll, 2000)` with no stored handle. `if (document.hidden) return` skips the *fetch* but the timer keeps firing forever. And `backgroundActivitySeen` (script.js:30) is a `Set` that grows unboundedly for the life of the tab.
- **The notification EventSource reconnect (script.js:2889) stores no handle** — a flapping server produces overlapping reconnect timers.
- **The stream timeout is 300 seconds** (script.js:2225, 1380) and it is an *absolute* timeout, not an idle timeout. A stream that dies silently at second 3 hangs the UI for 5 minutes.
- **`orb.js` renders continuously at full `devicePixelRatio`** with no visibility check. On a 150% Windows scale factor at 600 CSS px that is a 900×900 fragment shader with 3D simplex noise, **running forever, including when the tab is hidden or the orb is fully covered by an open panel.** `_loop` (orb.js:~330) calls `requestAnimationFrame` unconditionally. This is the largest continuous cost in the app.
- **`script.js:665` reassigns `ORB_STATES`**, a mutable `let` exported implicitly from `orb.js:31`. That cross-file mutable global is the one hard blocker for ES modules, and `initOrb` (script.js:715) additionally **monkey-patches** `orb.setState` / `orb.setStateInstant` to poke the status badge. Both must be replaced with real APIs in P9.
- **`preloadStarterAudio()` (script.js:311) fires 10 sequential `POST /tts` requests on every single page load.** They hit the server-side voice cache so they are cheap server-side, but it is 10 round trips and 10 base64 blobs in memory before the user has typed anything.

---

## 2. GROUND RULES — READ BEFORE WRITING CODE

These are constraints, not suggestions. The user's explicit requirement is: **make it better without
making the machine slower.** This machine is simultaneously running the FastAPI server, a
sentence-transformers embedding model, FAISS, a UIA COM STA thread, a file-index builder and a
watcher daemon. The browser gets whatever is left.

### 2.1 DO NOT

1. **No frontend framework.** No React, Vue, Svelte, Preact, Alpine, htmx, lit. `CLAUDE.md` §19 records this as a deliberate decision. It stands.
2. **No build step, no bundler, no transpiler, no `node_modules`, no `package.json`.** ES modules are served natively by `StaticFiles`. That is the whole toolchain.
3. **No new runtime dependency of any kind** — not `marked`, not `DOMPurify`, not `highlight.js`, not `anime.js`, not a CSS framework, not a webfont beyond the Poppins already loaded. Every feature in this plan is implementable in vanilla JS/CSS and is specified that way. If you think you need a library, you have misread the spec.
4. **Do not add a second polling loop, anywhere.** Same principle as `CLAUDE.md` rule #11 (never add a second watcher poller). The frontend has exactly one activity poller and one notification EventSource. New data hooks the existing ones.
5. **Do not add features the user did not ask for.** No theme marketplace, no plugin system, no analytics, no onboarding tour, no confetti, no sound design beyond what exists. The feature list in P10 is closed.
6. **Do not "improve" the visual identity.** The space-purple glass + WebGL orb *is* the product's identity and the user likes it. Every change in this plan either (a) reduces cost while preserving the look, or (b) fixes a broken interaction. Nothing changes the aesthetic direction.
7. **Do not use `innerHTML` with any string that contains server, LLM, or user data.** Ever. Build DOM with `createElement` + `textContent`, or use the escaper. This is not negotiable — see P4.
8. **Do not hardcode lists that duplicate a source of truth.** Same spirit as `CLAUDE.md` rule #10. The command palette (P10.2) must read from a registry that each module contributes to, not from a hand-maintained array in one file. The keyboard-shortcut overlay (P10.3) must render from that same registry, not from a second hand-written list.
9. **Do not change any API endpoint's request or response shape.** The frontend adapts to the backend, not the reverse. The only backend edits allowed are the four additive ones in P7.3 and P12.4.
10. **Do not delete `viewer.html`'s call to `/tasks/{task_id}`** without reading `CLAUDE.md` §18 issue 5 first — the route genuinely does not exist, so that page is partly broken. P11 handles it. Do not silently "fix" it by inventing a backend route.

### 2.2 DO

1. **Measure before and after.** P0 sets up the measurement. A phase that claims a performance win without a before/after number is not done.
2. **Prefer deleting over adding.** P1 deletes ~900 lines of CSS and P5 deletes ~90 lines of duplicated JS. Those are the two best phases in this plan.
3. **Comment the *why*, not the *what*.** Match the existing style in `script.js` and `CLAUDE.md`: the codebase already documents load-bearing subtleties inline (e.g. the `<base href>` comment in `index.html:5-7`, the `.history-item.menu-open` z-index note). Continue that. Every trap you fix gets a comment explaining what breaks if it is reverted.
4. **Keep every change fail-soft.** `CLAUDE.md` rule #6. If the markdown renderer throws, the message must still show as plain text. If the command palette fails to init, chat must still work. Wrap new subsystems in `try/catch` at their init boundary.
5. **Respect `prefers-reduced-motion` in every new animation you write.** No exceptions.
6. **Keep ARIA parity.** The existing UI has genuinely good ARIA (`aria-live` toasts, `aria-hidden` panels, `aria-expanded` on toggles, `role="status"`, `sr-only` announcer). Every new interactive element matches that standard or better.

### 2.3 Performance budget

Targets on the user's own machine, in Chrome/Edge, with the server running.

| Metric | Current (measure in P0) | Target after P2 | Hard ceiling |
|---|---|---|---|
| Idle CPU, tab focused, no chat activity | measure | **< 40% of current** | < 3% of one core |
| Idle GPU process, tab focused | measure | **< 50% of current** | — |
| Idle CPU, tab hidden | measure | **≈ 0%** | < 0.5% |
| Frame time while streaming a long reply | measure | **< 16.7ms p95** | < 33ms p95 |
| Frame time while dragging a floating panel | measure | < 16.7ms p95 | < 33ms p95 |
| First paint → orb visible | measure | no regression | — |
| `style.css` transfer size | ~106 KB | **< 80 KB** | — |
| Total JS transfer size | ~135 KB | no more than +10% | — |
| Long tasks (>50ms) during a chat turn | measure | **0** | ≤ 1 |

**The single most important number is "idle CPU, tab hidden".** Right now the orb render loop, both
star-field animations, `logoSheen`, `pulse-dot`, and the 2-second activity timer all keep running
when the user has switched to another window. On a machine that is also running an embedding model,
that is pure theft. After P2.3 it must be effectively zero.

---

## 3. PHASE MAP

| Phase | Name | Effort | Impact | Risk | Depends on |
|---|---|---|---|---|---|
| **P0** | Baseline, safety net, measurement harness | S | — | none | — |
| **P1** | Delete the duplicated CSS block | S | **Very high** | low | P0 |
| **P2** | Motion, GPU and idle-cost reduction | M | **Very high** | low | P1 |
| **P3** | Streaming correctness: cursor leak, error recovery, stop button, idle timeout | M | **Very high** | med | P0 |
| **P4** | XSS and unsafe-`innerHTML` fixes | S | **High** (security) | low | P0 |
| **P5** | Merge `sendMessage` + `sendMessageWithImage` | S | High (maintainability) | med | P3 |
| **P6** | Streaming-safe markdown rendering | L | **Very high** (UX) | med | P4, P5 |
| **P7** | Polling, timers and listener hygiene | M | High | low | P0 |
| **P8** | Design tokens, z-index scale, CSS file split | M | Med | low | P1, P2 |
| **P9** | `script.js` → ES modules | L | Med (maintainability) | **high** | P1–P8 |
| **P10** | UX features: header density, command palette, shortcuts overlay, panel memory, icon consistency, toast queue | L | High | med | P9 |
| **P11** | Cross-surface consistency: admin CSS, `viewer.html`, `api-monitor` | M | Med | low | P8 |
| **P12** | Accessibility + asset versioning pass | M | Med | low | P10 |

**If you only have time for three phases: P1, P2, P3.**
**If you only have time for one: P1.**

---

## P0 — BASELINE, SAFETY NET, MEASUREMENT HARNESS

**Goal:** Be able to prove every later claim, and be able to undo anything.
**Why:** Every phase after this makes a performance or correctness claim. Without a baseline those
claims are decoration. And a 900-line CSS deletion without a visual reference is reckless.

### Files
- new: `docs/UI_BASELINE.md`
- new: `web/js/devtools.js` *(dev-only, gated — see P0.4)*
- edit: `web/index.html` (one `<script>` tag, last line of `<body>`)

### Steps

**P0.1 — Commit the current state and confirm the tree is clean.**
```bash
git status --short
git add -A
git commit -m "M14 P0: checkpoint before UI work"
git rev-parse --short HEAD
```
Record that SHA at the top of `docs/UI_BASELINE.md`. That is the rollback target for the whole plan.

**P0.2 — Capture a visual reference set.**
Start the server (`start.bat`). With the browser at `http://localhost:8000/jarvis/`, capture a
full-page screenshot of each of these states and save to `docs/ui_baseline/` (create the folder):

| File | State |
|---|---|
| `01-welcome.png` | Fresh load, welcome screen with all 6 chips |
| `02-chat-streaming.png` | Mid-reply, cursor visible, activity panel open |
| `03-chat-done.png` | Completed multi-paragraph reply |
| `04-history-open.png` | History drawer open with several conversations |
| `05-history-menu.png` | A history row's ellipsis menu open |
| `06-history-dialogs.png` | Rename dialog open, then delete dialog open (2 shots) |
| `07-activity-panel.png` | Activity panel with ≥10 rows including a FAIL |
| `08-search-results.png` | Search-results widget populated |
| `09-settings.png` | Settings panel open |
| `10-orb-dashboard.png` | Orb dashboard open, a state tab selected |
| `11-cam-panel.png` | Camera panel open, video live, then minimized (2 shots) |
| `12-reminders.png` | Reminders panel with ≥2 reminders, one recurring, one overdue |
| `13-notes.png` | Notes tab with ≥2 notes |
| `14-todos.png` | To-Do tab with a list, some items done |
| `15-reminder-toast.png` | A fired reminder toast |
| `16-toast.png` | A plain toast |
| `17-mobile-768.png` | DevTools responsive at 768px wide, chat + history open |
| `18-mobile-480.png` | Responsive at 480px, chat + composer |
| `19-orb-states.png` | Each of the 6 orb states (use the dashboard tabs) — 6 shots or one contact sheet |
| `20-dashboard.png` | `/dashboard` Control Center |
| `21-watcher.png` | `/watcher` |
| `22-api-monitor.png` | `/jarvis/api-monitor.html` |
| `23-viewer.png` | `/jarvis/viewer.html` |

⚠ **TRAP:** `06`, `11`, `12`, `19` are the states most likely to break in P1 and P2, because
`.history-dialog-backdrop`, `.cam-panel.minimized`, `.reminder-card` and `#orb-container.orb-*`
are exactly the selectors touched by the duplicate-block deletion and the motion work. Do not skip them.

**P0.3 — Record baseline numbers into `docs/UI_BASELINE.md`.**

Use Chrome DevTools. For each measurement, state the method so it is reproducible.

1. **Idle CPU / GPU, tab focused.** Open the app, do nothing for 30s. Chrome Task Manager
   (`Shift+Esc`) → record the CPU% for the tab row and for the "GPU Process" row. Take the median
   of three 10-second reads.
2. **Idle CPU, tab hidden.** Same, but switch to another application window for 30s first, then
   read Chrome Task Manager (it keeps updating). Record.
3. **Idle CPU with an open panel covering the orb.** Open the orb dashboard, wait 30s, read.
   *(This tells you how much the orb costs when it is not even visible.)*
4. **Streaming frame time.** DevTools → Performance → record → send a prompt that produces a long
   reply (e.g. "Write me 600 words about the history of the printing press") → stop recording when
   the reply finishes. Record: total long tasks (>50ms), p95 frame time, and the "Scripting" vs
   "Rendering" vs "Painting" split from the Summary donut.
5. **Panel drag frame time.** Performance record → drag the reminders panel around the screen for
   5 seconds → stop. Record p95 frame time.
6. **Asset sizes.** Network tab, hard reload, disable cache. Record transfer size of `style.css`,
   `script.js`, `orb.js`, and the total.
7. **First paint.** Performance panel → reload → record FCP and the time until the orb canvas first
   draws.
8. **Layout thrash count during streaming.** In the Performance recording, look for purple
   "Recalculate Style" / "Layout" bars during the streaming section. Record roughly how many per
   second. *(Expect a lot — see P3.1 for why.)*

Write all of it in a table. Include your machine's DPR (`window.devicePixelRatio` in the console) —
it changes the orb's real pixel cost and you need it to interpret P2.3.

**P0.4 — Add a dev-only FPS/perf overlay.**

Create `web/js/devtools.js`. It must be **completely inert unless explicitly enabled**, so it can
ship without cost.

```js
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
```

⚠ **TRAP:** `web/js/` does not exist yet and `index.html` currently loads classic scripts, not
modules. Until P9 converts `script.js`, load this one file as a module with its own tag — module
and classic scripts coexist fine:

In `index.html`, immediately before `</body>` (after the existing `script.js` tag):
```html
    <!-- Dev-only perf overlay (M14 P0). Inert unless ?perf=1 or
         localStorage.jarvis_perf === '1'. See web/js/devtools.js. -->
    <script type="module">
        import { initDevtools } from './js/devtools.js?v=20260801';
        try { initDevtools(); } catch (e) { console.warn('[perf] init failed', e); }
    </script>
```

⚠ **TRAP:** the `./js/...` path resolves against `<base href="/jarvis/">`, so it becomes
`/jarvis/js/devtools.js`. That is correct and it works at the deep-link URL `/jarvis/c/<id>` too —
which is precisely why the `<base>` tag exists. **Do not** write `/js/devtools.js` (absolute) — the
app is mounted at both `/jarvis` and `/app` and an absolute path breaks the `/app` mount.

**P0.5 — Record the static-check commands you will run after every phase.**

```bash
# from the repo root
node --check web/script.js
node --check web/orb.js
node --check web/api-monitor.js
python -m compileall -q app tests scripts run.py config.py
python -m pytest tests/ -q
```

`node --check` does **not** understand ES modules in all Node versions; from P9 onward use:
```bash
node --input-type=module --check < web/js/chat/stream.js    # per module
# or simply, for any file with import/export:
node --experimental-detect-module --check web/js/<file>.js
```
If your Node is old enough that neither works, fall back to loading the page and confirming zero
console errors — but say so in the phase report rather than claiming the check passed.

### Verify
- `docs/UI_BASELINE.md` exists with all 8 measurements filled in and the DPR recorded.
- `docs/ui_baseline/` has all listed screenshots.
- Loading the app **without** `?perf=1` shows no overlay and adds no rAF loop (confirm: DevTools
  Performance record for 3s, "Animation Frame Fired" entries should come only from `orb.js`).
- Loading with `?perf=1` shows the overlay and it updates once per second.
- All static checks pass. 215 tests still pass.

### Rollback
`git reset --hard <P0 SHA>` — but there is nothing to roll back; P0 is purely additive.

---

## P1 — DELETE THE DUPLICATED CSS BLOCK

**Goal:** Remove ~900 dead lines and make `style.css` lines 1290–2063 editable again.
**Why:** `style.css` lines **2430–3331** are a stale re-paste of **1290–2063** plus re-pasted copies
of both media queries. Today the values match so nothing looks wrong — but any edit you make to the
activity panel, search-results widget, settings panel, or the core keyframes in the *first* copy is
**silently overridden by the second copy further down the cascade.** Every later phase in this plan
edits rules inside that range. If you skip P1, P2 and P8 will appear to do nothing and you will
waste hours.

This is the highest value-to-effort action in the entire plan.

### Files
- edit: `web/style.css`

### Evidence (verified, not assumed)
- Copy 1: lines **1290–2063**. Copy 2: lines **2430–3331**.
- 97 rules in copy 1; 87 in copy 2; **all 87 are byte-equivalent** to their copy-1 twin.
- Copy 2 is an **older** snapshot. It is missing exactly these 10 rules that were added later:
  ```
  .activity-item.route-vision   .activity-event / .activity-step   (1429–1431)
  .activity-item.route-task     .activity-event / .activity-step   (1432–1434)
  .activity-item.route-chat     .activity-event / .activity-step   (1435–1437)
  .activity-item.route-tts-hit   …                                 (1439–1440)
  .activity-item.route-tts-saved …                                 (1443–1444)
  ```
  It also predates the Orb Dashboard block (1767–2020) and the UI-POLISH block (2065–2132).
- Re-pasted media queries inside the dead range: `@media (max-width: 768px)` at **3027** is
  byte-identical to **2134**. `@media (max-width: 480px)` at **3164** equals **2166** except it is
  *missing* `.message { max-width: 95% }` and `.msg-content { max-width: 100% }`.
- Duplicated `@keyframes` (later definition wins, earlier is dead):
  `fadeIn` 2033/**2932** · `msgIn` 2037/**2944** · `dotBounce` 2041/**2956** · `blink` 2045/**2971** ·
  `pulse-dot` 2048/**2977** · `micPulse` 2052/**2989** · `ttsPulse` 2056/**3001** ·
  `orbPulse` 2060/**3013** · `activityIn` 1454/**2593**
- `@supports (scrollbar-color: ...)` exists twice: **1652** (keep) and **2798** (dead).
- Two `.toggle-switch` rules inside the dead range also exist at **1755** and **1759**. Nothing unique is lost.

### Steps

**P1.1 — Prove the range is redundant *on your tree* before deleting it.**

Do not trust this document blindly; the file may have changed. Run this from the repo root:

```powershell
# Extract both candidate ranges to temp files and eyeball the rule inventory.
Get-Content web\style.css | Select-Object -Skip 1289 -First 774 | Set-Content $env:TEMP\css_copy1.txt
Get-Content web\style.css | Select-Object -Skip 2429 -First 902 | Set-Content $env:TEMP\css_copy2.txt

# List selectors in each (crude but sufficient: lines ending in '{')
Select-String -Path $env:TEMP\css_copy1.txt -Pattern '\{\s*$' | ForEach-Object { $_.Line.Trim() } | Sort-Object -Unique | Set-Content $env:TEMP\sel1.txt
Select-String -Path $env:TEMP\css_copy2.txt -Pattern '\{\s*$' | ForEach-Object { $_.Line.Trim() } | Sort-Object -Unique | Set-Content $env:TEMP\sel2.txt

# Anything in copy2 that is NOT in copy1 is a rule you would lose. Expect: only
# the two re-pasted @media lines and the @supports line.
Compare-Object (Get-Content $env:TEMP\sel1.txt) (Get-Content $env:TEMP\sel2.txt) | Where-Object SideIndicator -eq '=>'
```

If that last command outputs **any selector that is not a `@media`, `@supports`, or `:root` line**,
**STOP** and report it. Something has changed since this plan was written and the deletion is no
longer safe as specified.

**P1.2 — Locate the exact boundaries by content, not by number.**

The block to delete **starts** at the line that opens the second `.activity-panel` rule. Search for
the *second* occurrence of:
```css
.activity-panel {
    position: fixed;
```
The block **ends** at the closing `}` of the second `@media (max-width: 480px)` block — i.e. the
line immediately before the `/* ── Orb Dashboard responsive ── */` comment that precedes the
`@media (max-width: 768px)` at 3334.

Confirm the boundary lines read exactly:
- First line of the deletion: the blank/comment line just above that second `.activity-panel {`
  (include any orphaned section banner directly above it).
- Last line of the deletion: the `}` that closes the 480px media query at ~3331.

⚠ **TRAP:** Do **not** delete past 3331. Line **3333–3352** is the *Orb Dashboard responsive*
`@media 768` block, which is **unique and load-bearing** — the orb dashboard's mobile layout lives
there and nowhere else. Deleting it silently breaks `10-orb-dashboard.png` at 768px.

⚠ **TRAP:** Do **not** start the deletion earlier than the second `.activity-panel`. Lines
**2206–2429** are the **API Monitor** styles (`.monitor-*`) used by `api-monitor.html`. They are
unique. Deleting them blanks that page.

**P1.3 — Delete the range, and leave a tombstone comment where it was.**

Replace the deleted block with exactly this, so the next person does not re-paste it:

```css
/* ═══════════════════════════════════════════════════════════════════
   [M14 P1] ~900 lines were deleted here on <DATE>.
   ───────────────────────────────────────────────────────────────────
   This position previously held a stale, byte-identical re-paste of the
   Activity panel / Search-results widget / Settings panel / core @keyframes
   block that lives above (see "Activity panel" onward). Because it came
   LATER in the cascade it silently won every conflict, so edits made to the
   real rules above appeared to do nothing.

   If you find yourself about to paste a block of existing CSS at the bottom
   of this file: don't. Edit the rule where it is defined. If you need to
   override intentionally, put it in web/css/overrides.css (see P8) so the
   intent is visible.
   ═══════════════════════════════════════════════════════════════════ */
```

**P1.4 — Resolve the orphan selectors.**

These are styled **only** inside media queries with no base rule anywhere. After P1 removes the
duplicate copies, each survives in exactly one or two places:

| Selector | Surviving lines (post-P1) | Action |
|---|---|---|
| `.speech-widget` | in `@media 768` and `@media 480` | **Investigate** |
| `.speech-widget-text` | same | **Investigate** |
| `.speech-widget-label` | in `@media 480` | **Investigate** |
| `.new-chat-btn` | in `@media 480` | Keep — the element exists (`index.html`, `#new-chat-btn`) |
| `.mode-btn-text` | in `@media 768` | Keep — the element exists (`index.html`, `.mode-btn-text`) |
| `.mode-switch-three .mode-btn` and `… .mode-btn-text` | base at 285 + media | Keep for now; flagged in P10.5 |

Investigation procedure for `.speech-widget*`:
```bash
# Does the element exist in any surface?
grep -rn "speech-widget" web/ app/static/
```
- If it appears **only** in `style.css` → the feature was removed. Delete all `.speech-widget*` rules
  and note the deletion in the phase commit message.
- If it appears in any HTML or JS → **do not delete.** Instead write the missing base rule, derived
  from what the media queries assume (they only set `font-size`, so a base rule with
  `font-size: 0.9rem` plus whatever layout the element needs). Report that you did this.

**P1.5 — Delete the confirmed-dead `@keyframes dotBounce`.**

After P1.3 only one copy remains (originally at 2041). Confirm it is unreferenced:
```bash
grep -rn "dotBounce" web/ app/static/
```
Expect exactly one hit — its own `@keyframes` declaration. Delete it.

⚠ **TRAP:** the typing indicator uses `.typing-dot` with a *different* animation. Verify before
deleting: `grep -n "typing-dot" web/style.css`. If `.typing-dot` references `dotBounce`, the grep
above will show it and you must **keep** the keyframe. Trust the grep, not this table.

**P1.6 — Fold the UI-POLISH block back into its origin rules.**

Lines **2065–2132** are a self-declared "append-only, non-breaking" override block. It creates a
second definition of six selectors that already exist above. Merge each override into the original
rule and delete the block, keeping the keyframes it introduced.

| Override in the polish block | Merge into | What to merge |
|---|---|---|
| `.welcome-icon { animation: welcomeFloat 4s ... }` | `.welcome-icon` (558) | add the `animation` line |
| `.message.assistant .msg-avatar { box-shadow: 0 0 14px rgba(124,106,239,0.35) }` | `.message.assistant .msg-avatar` (677) | add the `box-shadow` |
| `.logo { background-size: 200% auto; animation: logoSheen 6s linear infinite }` | `.logo` (224) | add both lines |
| `.input-wrapper:focus-within { border-color; box-shadow }` | `.input-wrapper:focus-within` (1112) | **replace** the existing `border-color`/`box-shadow` with the polish values (the polish values are the intended ones — they are later in the cascade today) |
| `.status-badge { padding; border-radius; background; border }` | `.status-badge` (340) | **replace** those four declarations |
| `.activity-item { transition: background …, border-color …, transform … }` | `.activity-item` (1380) | **replace** the existing `transition` |
| `.message { animation: msgIn 0.4s cubic-bezier(0.22,1,0.36,1) }` | `.message` (650) | **replace** the existing `animation` |
| `.activity-item:hover { … }` | keep as its own rule, move it directly under `.activity-item` (1380) | move only |
| `:focus-visible` group (`.send-btn`, `.action-btn`, `.btn-icon`, `.chip`) | keep as its own rule | **move** it to sit with the other focus styles; do not lose it — it is the only `:focus-visible` in the file |
| `@keyframes welcomeFloat` | move next to `.welcome-icon` | move only |
| `@keyframes logoSheen` | move next to `.logo` | move only |

⚠ **TRAP:** the merge direction matters. Because the polish block came *later*, its values are the
ones currently rendering. Where a declaration exists in both, **the polish value wins** — carry the
polish value into the origin rule and delete the origin's version. Getting this backwards changes
the visual output, which is exactly what P1 must not do.

⚠ **TRAP:** `.logo` also appears in both media queries. Those override `font-size` only and are fine.
Do not touch them here.

**P1.7 — Remove the five removable `!important`s on `.send-btn`.**

At style.css **1167–1177**, `.send-btn` and `.send-btn:hover` carry 5 `!important`s whose only
purpose is to beat `.action-btn` (1142). `.send-btn` has the same specificity as `.action-btn` but
comes **later** in the file, so it already wins. Delete all five `!important` tokens.

⚠ **TRAP:** **Keep** the two `!important`s on `.cam-panel.minimized` (931–932). They exist to beat
**inline** `width`/`height` written by the JS resize handler (`makeResizable`, script.js:2440, and
the bespoke cam resize at script.js:1131–1143). Inline styles beat any selector, so those two are
genuinely load-bearing. **Keep** the `[hidden]` one at 4091 — it is documented in place and beats
`display` rules that would otherwise leave dialogs permanently visible (`CLAUDE.md` §13 records this).

**P1.8 — Fix the split base rules.**

Purely cosmetic in output, but they cause exactly the confusion that produced the duplicate block.

| Issue | Lines | Fix |
|---|---|---|
| `.cam-panel` defined twice at base level, 50 lines apart | 898 and 949 | merge into one rule at 898; the second only adds `display:flex; flex-direction:column` |
| `.activity-item.route-general .activity-step` split across two adjacent rules | 1423, 1424 | merge |
| `.activity-item.route-realtime .activity-step` | 1426, 1427 | merge |
| `.activity-item.route-vision …` | 1429, 1430 | merge |
| `.activity-item.route-task …` | 1432, 1433 | merge |
| `.activity-item.route-chat …` | 1435, 1436 | merge |
| `.activity-item` defined twice at base | 1380 and (post-P1.6) merged | already handled by P1.6 |

### Verify
- `web/style.css` is now **roughly 3,450–3,500 lines** (from 4470). Report the exact number.
- Transfer size of `style.css` dropped to **< 80 KB**. Record it.
- **Pixel-compare every screenshot from P0.2.** Re-take all 23+ shots and diff them against the
  baseline. The only acceptable differences are anti-aliasing noise. **Any real visual change means
  you deleted something unique — stop and find it.** This is the whole verification for P1; do not
  skip it and do not eyeball only the welcome screen.
- Specifically re-check the four high-risk states: history dialogs, camera panel minimized,
  reminders panel with a recurring + overdue reminder, and all 6 orb states.
- Check `/jarvis/api-monitor.html` still renders (proves you did not eat the `.monitor-*` block).
- Check 768px and 480px responsive views.
- `node --check` on the three JS files (unchanged, but cheap insurance).

### Rollback
`git checkout HEAD~1 -- web/style.css`

---

## P2 — MOTION, GPU, AND IDLE-COST REDUCTION

**Goal:** Cut idle CPU/GPU sharply, make hidden-tab cost ~zero, and honour `prefers-reduced-motion`
across the whole app — **without changing how the app looks when it is being used.**
**Why:** This is the user's explicit requirement. Right now the app burns GPU continuously even when
hidden, and the reduced-motion preference is honoured for exactly one component.

The strategy is: **keep every effect, but stop paying for it when nobody is looking at it.**

### Files
- edit: `web/style.css`
- edit: `web/orb.js`
- edit: `web/script.js`
- edit: `web/index.html` (one meta line)

### P2.1 — One global `prefers-reduced-motion` block

Currently: one block at the end of the file covering only `.history-*`. **22 of 23 infinite
animations and 71 of 76 transitions ignore the preference.**

Replace the block at (post-P1) end-of-file with this, and keep it as the **last** thing in
`style.css` so it wins the cascade:

```css
/* ═══════════════════════════════════════════════════════════════════
   Reduced motion — global.  [M14 P2.1]
   ───────────────────────────────────────────────────────────────────
   Before this existed, exactly one component (the history drawer) honoured
   the preference; 22 infinite animations kept running. This block is
   deliberately broad: kill everything, then re-permit the few effects that
   convey state rather than decoration.

   Keep this block LAST in the file. It works by source order, not
   specificity, so anything appended after it will beat it.
   ═══════════════════════════════════════════════════════════════════ */
@media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
        animation-duration: 0.001ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 0.001ms !important;
        scroll-behavior: auto !important;
    }

    /* The star field is pure decoration — stop it moving entirely.
       (P2.2 also makes it static for everyone; this is the belt to that's braces.) */
    body::before,
    body::after { animation: none !important; }

    /* The orb keeps its per-state colour and glow (that is information —
       it tells you what JARVIS is doing) but stops pulsing.
       The WebGL loop itself is throttled separately in orb.js — see P2.4. */
    #orb-container,
    #orb-container.active,
    #orb-container.speaking,
    #orb-container.orb-idle,
    #orb-container.orb-listening,
    #orb-container.orb-thinking,
    #orb-container.orb-searching,
    #orb-container.orb-working,
    #orb-container.orb-speaking { animation: none !important; }

    /* Kept on purpose — these communicate live state and a user who
       disabled motion still needs to know the system is busy.
       They are all small, non-translating, and low frequency. */
    .stream-cursor      { animation: blink 1s step-end infinite !important; }
    .bg-task-spinner    { animation: spin 1.2s linear infinite !important; }
    .status-dot         { animation: none !important; }   /* colour alone is enough */
}
```

⚠ **TRAP:** `animation-duration: 0.001ms` rather than `animation: none` for the universal selector
is deliberate. Several one-shot entrance animations set their element's *final* state in the
keyframe's `to` block (e.g. `fadeInUp` ends at `opacity: 1`). `animation: none` would leave those
elements stuck at the pre-animation state — invisible. `0.001ms` runs the animation instantly so the
end state is applied. This is the standard workaround and you must not "simplify" it.

⚠ **TRAP:** Do not add `!important` to the `.stream-cursor` / `.bg-task-spinner` re-permits without
also keeping them *after* the universal rule in the same block — `!important` ties are broken by
source order.

**Test it:** DevTools → `Ctrl+Shift+P` → "Show Rendering" → "Emulate CSS media feature
prefers-reduced-motion: reduce". Then: load the app, send a message, open every panel, fire a
reminder. Nothing should move except the stream cursor and any background-task spinner. Confirm no
element is invisible.

### P2.2 — Make the star field free

Currently: `body::before` and `body::after` are each `position: fixed`, `200% × 200%` (four times
viewport area), each holding 6–12 stacked `radial-gradient`s, each running an infinite
`transform: translate` animation (120s and 160s). Two 4×-viewport composited layers animating
forever. **This is the largest constant CSS cost in the app and its total visual contribution is
"there are faint dots".**

The drift is imperceptible anyway: `starDrift1` moves 30px over **120 seconds** — 0.25 px/sec.

**Do this:**

1. Delete both `animation:` lines (style.css ~64 and ~74).
2. Delete `@keyframes starDrift1` and `@keyframes starDrift2` (~77–85).
3. Shrink both pseudo-elements from `200%`/`-50%` to exactly cover the viewport, since they no longer
   need overscan for the translate:

```css
/* Static star field.  [M14 P2.2]
   Was: two fixed 200%x200% layers (4x viewport each) with infinite
   translate animations (120s / 160s = 0.25 px/sec — imperceptible).
   Two always-animating composited layers at 4x viewport area was the
   single largest constant GPU cost in the app for zero perceived benefit.
   Now static and viewport-sized: same look, no per-frame work at all. */
body::before,
body::after {
    content: '';
    position: fixed;
    inset: 0;
    pointer-events: none;
    z-index: -1;
}
```
Keep both `background:` declarations exactly as they are — the gradient positions are percentages,
so they still spread across the whole viewport.

⚠ **TRAP:** After removing the `top:-50%; left:-50%; width:200%; height:200%`, the gradient *stops*
will land at different absolute pixel positions than before, so the dot pattern will look different
(not worse — different). If the user objects, the alternative is to keep the 200% sizing and only
delete the animations; that preserves the exact pattern and still removes all per-frame cost.
**Default to the smaller version; mention the alternative in the phase report.**

⚠ **TRAP:** Do not merge the two pseudo-elements into one. Both are needed to stack the two gradient
sets; a single element can hold all 18 gradients but that makes one very long `background` value and
gains nothing.

### P2.3 — Stop the WebGL orb when nobody can see it

Currently: `orb.js` `_loop` unconditionally re-arms `requestAnimationFrame`. The browser *does*
throttle rAF in a fully hidden tab — but this app is used in a **desktop browser window that is
often behind other windows**, and in a covered-but-visible tab (e.g. orb dashboard open over it)
rAF keeps running at full rate. Combined with `devicePixelRatio` scaling, a 600 CSS-px orb on a
150% display is a **900×900** fragment shader evaluating 3D simplex noise every frame, forever.

Add four independent savings. Each is small; together they are the biggest win in P2.

**P2.3a — Cap the render resolution.**

In `orb.js`, `_resize()`:

```js
    _resize() {
        // Render-scale cap.  [M14 P2.3a]
        // The orb is a soft, noisy, blurred blob — it has no high-frequency
        // detail that benefits from a 2x or 3x device pixel ratio, but the
        // fragment shader cost scales with the square of it. At DPR 1.5 an
        // uncapped 600px orb is 900x900 = 810k fragments of 3D simplex noise
        // every frame. Capping at 1.25 keeps it visually identical and cuts
        // fragment count by ~2.5x on a 2x display.
        //
        // this.renderScale is further reduced by the adaptive quality
        // controller (P2.3d) when frames get expensive.
        const dpr = Math.min(window.devicePixelRatio || 1, this.maxDpr) * this.renderScale;
        const w = this.container.clientWidth;
        const h = this.container.clientHeight;
        this.canvas.width  = Math.max(1, Math.round(w * dpr));
        this.canvas.height = Math.max(1, Math.round(h * dpr));
        if (this.gl) this.gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    }
```

In the constructor, before `this._resize()`:
```js
        this.maxDpr      = opts.maxDpr ?? 1.25;   // see _resize()
        this.renderScale = 1.0;                    // adaptive, see P2.3d
```

⚠ **TRAP:** The shader reads `iResolution` and derives UVs from it (`orb.js` `mainImage`, using
`min(iResolution.x, iResolution.y)`). Because everything is normalised by the short axis, changing
the backing-store size does **not** change the orb's apparent size or shape. Verified by reading the
shader. Do not "compensate" for the scale anywhere.

**P2.3b — Pause on `document.hidden`.**

In the `OrbRenderer` constructor, after starting the loop:

```js
        // Visibility gating.  [M14 P2.3b]
        // Browsers throttle rAF in a hidden tab but do not stop it, and this
        // app is normally a desktop window sitting behind other windows.
        // Fully stopping the loop is the difference between ~0% and several
        // percent of a core on a machine that is also running an embedding
        // model and a UIA COM thread.
        this._onVisibility = () => {
            if (document.hidden) this._pause('hidden');
            else this._resume('hidden');
        };
        document.addEventListener('visibilitychange', this._onVisibility);
```

Add the pause/resume machinery to the class. Use a **reason set**, not a boolean, so the three
independent pause sources (hidden, occluded, reduced-motion) cannot un-pause each other:

```js
    /* ── Pause / resume with reason tracking ──
     * Three independent things can pause the orb: the tab going hidden
     * (P2.3b), the orb being fully covered by a panel (P2.3c), and the user
     * preferring reduced motion (P2.3e). A single boolean would let whichever
     * one resumed last cancel the others. */
    _pause(reason) {
        this._pauseReasons ??= new Set();
        this._pauseReasons.add(reason);
        if (this._raf) {
            cancelAnimationFrame(this._raf);
            this._raf = null;
        }
    }

    _resume(reason) {
        this._pauseReasons ??= new Set();
        this._pauseReasons.delete(reason);
        if (this._pauseReasons.size === 0 && !this._raf) {
            // Reset lastTs so the first frame after a resume does not see a
            // multi-second dt and snap every lerped property to its target.
            this.lastTs = 0;
            this._raf = requestAnimationFrame(this._loop.bind(this));
        }
    }

    get paused() {
        return !!(this._pauseReasons && this._pauseReasons.size);
    }
```

⚠ **TRAP:** `this.lastTs = 0` on resume is essential. `_loop` computes
`const dt = this.lastTs ? t - this.lastTs : 0.016;`. After a 5-minute pause, `dt` would be `300`,
and `alpha = Math.min(dt * this.lerpRate, 1)` clamps to 1 — every lerped property jumps instantly.
Worse, `this.currentRot += dt * this.currentRotSpeed` would add hundreds of radians in one frame.
Resetting `lastTs` makes the first frame use the 0.016 default. **Do not omit this line.**

⚠ **TRAP:** `destroy()` must remove the new listener. Update it:
```js
    destroy() {
        if (this._raf) cancelAnimationFrame(this._raf);
        window.removeEventListener('resize', this._onResize);
        document.removeEventListener('visibilitychange', this._onVisibility);
        if (this._io) this._io.disconnect();                 // P2.3c
        if (this._mqMotion && this._onMotionPref) {          // P2.3e
            this._mqMotion.removeEventListener('change', this._onMotionPref);
        }
        if (this.canvas.parentNode) this.canvas.parentNode.removeChild(this.canvas);
        const ext = this.gl && this.gl.getExtension('WEBGL_lose_context');
        if (ext) ext.loseContext();
    }
```
Note the existing `destroy()` calls `cancelAnimationFrame(this._raf)` with a possibly-`null`
`_raf` after this change — `cancelAnimationFrame(null)` is harmless, but the guard above is clearer.

**P2.3c — Pause when the orb is off-screen or fully covered.**

`IntersectionObserver` handles off-screen. It does **not** detect occlusion by another element, so
handle the covered case explicitly: the orb sits at `z-index: 0` behind everything, and the two
things that fully cover it are the orb dashboard (`z-index: 25`, `100vh`, full-height right panel)
and the settings panel. Rather than guessing geometry, drive it from the same place that opens
those panels.

In `orb.js` constructor:
```js
        // Off-screen gating.  [M14 P2.3c]
        // The orb is position:fixed and centred, so it is almost always
        // intersecting — but this covers the case of a very short window and
        // costs nothing when it never fires.
        try {
            this._io = new IntersectionObserver((entries) => {
                for (const e of entries) {
                    if (e.isIntersecting) this._resume('offscreen');
                    else this._pause('offscreen');
                }
            }, { threshold: 0 });
            this._io.observe(this.container);
        } catch (_) { /* no IO support — orb simply never pauses for this reason */ }
```

Expose a public method for the occlusion case:
```js
    /* ── setOccluded: caller tells us the orb is fully hidden behind UI ──
     * Used by the orb dashboard and settings panel, which are full-height
     * opaque overlays. There is no browser API for "is this element visually
     * covered", so the panels that cover it report it. */
    setOccluded(occluded) {
        if (occluded) this._pause('occluded');
        else this._resume('occluded');
    }
```

⚠ **TRAP:** the **orb dashboard is the one panel that must NOT set `occluded`** — its entire purpose
is live orb preview. Only the settings panel and any future full-screen overlay should. Wire it in
`script.js` where the settings panel opens/closes (`bindEvents`, around script.js:1588–1607):

```js
// Orb pauses while a full-screen opaque panel covers it. The orb dashboard is
// deliberately excluded — live preview is the point of that panel.
if (orb) orb.setOccluded(settingsPanel.classList.contains('open'));
```
Do this in both the open and the close handler (or better, in `updatePanelOverlay()` at
script.js:1638, which already runs on every panel state change — read the settings panel's state
there and call `setOccluded` once. That is the DRY option; prefer it).

**P2.3d — Adaptive quality: downgrade when frames get expensive.**

This is the "smart, not hardcoded" piece the user asked for. Instead of a fixed quality setting,
measure and react.

Add to `orb.js`:
```js
    /* ── Adaptive quality ──  [M14 P2.3d]
     * The user's machine also runs the FastAPI server, a sentence-transformers
     * model, FAISS, a UIA COM thread and a file indexer. How much GPU is left
     * for a fragment shader is not knowable up front and changes minute to
     * minute, so a fixed quality setting is the wrong tool.
     *
     * We watch a rolling average of frame cost and step renderScale down when
     * we are consistently missing frames, back up when we are consistently
     * comfortable. Hysteresis (different up/down thresholds + a cooldown)
     * stops it oscillating.
     *
     * Deliberately NOT configurable in the orb dashboard: it is a safety
     * mechanism, not a preference. Users who want a bigger/faster orb already
     * have Size and Speed sliders.
     */
    _adapt(dt) {
        // dt is seconds. Convert to ms and keep an exponential moving average.
        const ms = dt * 1000;
        this._frameAvg = this._frameAvg == null ? ms : this._frameAvg * 0.9 + ms * 0.1;
        this._adaptCooldown = (this._adaptCooldown ?? 0) - dt;
        if (this._adaptCooldown > 0) return;

        const SCALE_MIN = 0.5;
        const SCALE_MAX = 1.0;
        const SCALE_STEP = 0.125;

        if (this._frameAvg > 22 && this.renderScale > SCALE_MIN) {
            // Consistently below ~45fps: shed pixels.
            this.renderScale = Math.max(SCALE_MIN, this.renderScale - SCALE_STEP);
            this._resize();
            this._adaptCooldown = 3;    // seconds before reconsidering
        } else if (this._frameAvg < 13 && this.renderScale < SCALE_MAX) {
            // Comfortably above 75fps: we can afford to look better.
            this.renderScale = Math.min(SCALE_MAX, this.renderScale + SCALE_STEP);
            this._resize();
            this._adaptCooldown = 8;    // slower to upgrade than to downgrade
        }
    }
```
Call it from `_loop`, right after `this.lastTs = t;`:
```js
        this._adapt(dt);
```

⚠ **TRAP:** `dt` in `_loop` is the *frame interval*, which is capped by vsync — it tells you the
frame **rate**, not the orb's own cost. That is fine here: we only care whether the machine is
keeping up overall. Do **not** try to measure GPU time with `gl.finish()` — it stalls the pipeline
and makes things worse.

⚠ **TRAP:** `_resize()` inside `_adapt` reallocates the canvas backing store. Do not let the
cooldown go below ~2s or you will thrash allocations. The 3s/8s values above are deliberate.

**P2.3e — Respect reduced motion in the shader loop.**

CSS `prefers-reduced-motion` cannot stop a WebGL loop. Do it in JS.

In `orb.js` constructor:
```js
        // Reduced motion.  [M14 P2.3e]
        // A user who asked the OS for less motion should not get a
        // continuously churning shader. We keep the orb visible with its
        // per-state colour (that is information, not decoration) but render it
        // as a still image: draw one frame, then stop.
        try {
            this._mqMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
            this._onMotionPref = () => {
                if (this._mqMotion.matches) {
                    // Draw one frame at the current state, then stop.
                    this._renderOnce();
                    this._pause('reduced-motion');
                } else {
                    this._resume('reduced-motion');
                }
            };
            this._mqMotion.addEventListener('change', this._onMotionPref);
            if (this._mqMotion.matches) {
                // Defer so _build()/_resize() have completed.
                requestAnimationFrame(() => this._onMotionPref());
            }
        } catch (_) { /* no matchMedia — orb animates as before */ }
```

Add `_renderOnce()` — factor the drawing half of `_loop` out so both can use it:
```js
    /* ── _renderOnce: draw exactly one frame at current values ──
     * Used when reduced-motion is on (still image) and when a state changes
     * while paused, so the orb's colour still updates to reflect what JARVIS
     * is doing even though it is not animating. */
    _renderOnce() {
        if (!this.pgm) return;
        this._draw(this.lastTs || 0);
    }
```
Refactor `_loop` so the GL calls live in `_draw(t)` and `_loop` becomes:
```js
    _loop(ts) {
        this._raf = requestAnimationFrame(this._loop.bind(this));
        if (!this.pgm) return;
        const t = ts * 0.001;
        const dt = this.lastTs ? t - this.lastTs : 0.016;
        this.lastTs = t;
        this._adapt(dt);
        this._lerp(dt);
        this._draw(t);
    }
```
where `_lerp(dt)` holds the eight `current* += (target* - current*) * alpha` lines plus
`this.currentRot += dt * this.currentRotSpeed`, and `_draw(t)` holds everything from
`gl.clear(...)` to `gl.drawArrays(...)`.

⚠ **TRAP:** When paused for reduced-motion, `setState()` must still update the colour. Add to the
end of both `setState` and `setStateInstant`:
```js
        // If we are paused (reduced motion / hidden / occluded), the loop will
        // not pick this up. Snap and draw one frame so the orb's colour still
        // reports what JARVIS is doing.
        if (this.paused) {
            this._lerpSnap();   // set current* = target* immediately
            this._renderOnce();
        }
```
`_lerpSnap()` assigns each `current*` from its `target*`. (`setStateInstant` already does this, so
there it is a no-op — call it anyway for symmetry, or guard it. Either is fine; be consistent.)

### P2.4 — Reduce the per-frame CSS cost around the orb

Independent of the WebGL loop, the orb's **container** is expensive.

| Current | Line | Problem | Fix |
|---|---|---|---|
| `transition: opacity 0.5s, transform 0.5s, filter 0.5s` | 118 | Animating `filter` on a 600px fixed element forces full re-rasterisation every frame of the transition, 6× per state change (once per drop-shadow layer) | Drop `filter` from the transition list. Keep `opacity` and `transform`. The glow colour will snap instead of cross-fading — barely noticeable, and the ambient-light `::before` still cross-fades over 1s and carries the colour transition. |
| Two stacked `drop-shadow()` per state, up to `0 0 70px` | 155–192 | Two stacked large-radius drop-shadows on an element that is *also* scaling via `orbPulse` — the most expensive single construct in the stylesheet | Collapse to **one** `drop-shadow` per state, at the larger radius and a slightly higher alpha to compensate. E.g. working: `drop-shadow(0 0 40px rgba(240,160,60,0.42))` replaces the 35px+70px pair. |
| `#orb-container::before` is `200% × 200%` of a 600px box (up to 1200×1200) | 127–128 | A 1200px radial gradient with `transition: background 1s` | Reduce to `150% × 150%`. Visually near-identical (the gradient fades to transparent at 70% anyway, so the outer 30% of the box is already invisible). |
| No `will-change` / `contain` anywhere | — | Every panel open/close allocates and destroys a compositor layer | Add `will-change: transform` **only** to the elements that actually slide, and **only** while they slide (see below). |

For `will-change`, the correct pattern is to add it on interaction and remove it after — a permanent
`will-change` keeps a layer alive forever, which is its own cost. Use a CSS-only approach that is
scoped to the transitioning state:

```css
/* Layer promotion, scoped.  [M14 P2.4]
   will-change is a promise to the compositor, not a free optimisation — a
   permanent one pins a layer in memory for the life of the page. These
   elements only need promotion while they are actually sliding, and every one
   of them gets an `.open` class toggled by JS, so we can scope it. */
.activity-panel,
.search-results-widget,
.history-panel,
.orb-dashboard,
.jarvis-panel,
.cam-panel {
    will-change: auto;
}
.activity-panel.open,
.search-results-widget.open,
.history-panel.open,
.orb-dashboard.open,
.jarvis-panel.open,
.cam-panel.open {
    will-change: transform;
}
```

⚠ **TRAP:** verify the actual class names before writing this. From `index.html` and `script.js`
the panels use `.open` (e.g. `activityPanel.classList.add('open')` at script.js:2216) but
`.cam-panel` and `.jarvis-panel` may use `aria-hidden` instead (`remindersPanel.getAttribute('aria-hidden') === 'false'` at script.js:2878). **Grep for how each panel is shown and match it.** If a
panel is toggled by `aria-hidden`, use `[aria-hidden="false"]` as the selector.

⚠ **TRAP:** Do not add `will-change` to `#orb-container`. It already has a WebGL canvas child, which
is always its own layer. Adding it would create a redundant layer.

Also add `contain` to the scrolling lists — this is a genuine, cheap win because it tells the browser
that layout changes inside cannot affect anything outside:
```css
/* Containment on the scrolling lists.  [M14 P2.4]
   Each of these gets rows appended while a turn runs. `contain: layout paint`
   stops an append inside them from invalidating layout for the whole page. */
.activity-list,
.history-list,
.search-results-list,
.jarvis-panel-body { contain: layout paint; }
```

⚠ **TRAP:** do **not** put `contain: layout paint` on `.chat-messages`. It is the scroll container
whose `scrollHeight` the streaming code reads; `contain: paint` creates a containing block that can
change how `position: fixed` descendants resolve, and `.scroll-fab` lives inside `.chat-area`.
Test that specific interaction if you try it. Default: leave `.chat-messages` alone.

### P2.5 — Reduce blur radii where they cost most

`backdrop-filter` cost scales with **both** blurred area and radius, and stacked blur layers
multiply ([reference](https://hyperframes.heygen.com/guides/performance) — cost scales with area and
radius, and stacked layers multiply; content was rephrased for compliance with licensing
restrictions). Three sites are disproportionate:

| Selector | Line | Now | Change to | Why |
|---|---|---|---|---|
| `.header` | 209–210 | `blur(48px) saturate(1.4)` | `blur(20px) saturate(1.2)` | Full viewport width × 64px, always visible, **sits above the scrolling chat so it re-resolves the blur on every scroll frame.** Largest radius in the file. At 20px the frosted look is preserved; the difference is not perceptible against a near-black background. |
| `.cam-panel` | 915–916 | `blur(28px)` | **remove `backdrop-filter` entirely**; raise the background opacity instead: `background: rgba(10,10,28,0.92)` | This panel contains a **live `<video>`**. The video repaints at camera framerate, so the blur behind it re-resolves every camera frame. It is the only per-frame blur recomputation in the app and it runs the whole time the camera is open. Nothing behind a 420×315 opaque-ish panel is worth seeing anyway. |
| `.history-dialog-backdrop` | 4379 | `blur(4px)` on `fixed; inset: 0` | keep the blur but add `background: rgba(0,0,0,0.45)` if not already present, and drop to `blur(3px)` | Full-viewport blur. It is modal and short-lived so the cost is acceptable, but it currently sits over an animating orb, which means the blur re-resolves every orb frame. **P2.3c's `setOccluded` should also fire for this dialog** — add it. |
| `.history-overlay` | 4101 | `blur(2px)` full-viewport, mobile only | keep | 2px over a mobile-only overlay is cheap. No change. |
| `.chip` | 611 | `blur(12px)` × 6 simultaneously | keep the blur, but see P2.6 for `transition: all` | 6 small elements, only on the welcome screen. Acceptable. |
| `.glass-panel` | 92–93 | `blur(32px) saturate(1.2)` | `blur(24px) saturate(1.15)` | Utility class applied to header, history panel, activity panel, dialogs — so it **multiplies**. 24px is still unmistakably frosted. |

⚠ **TRAP:** `.header` has **both** `.glass-panel` and its own `backdrop-filter`. The `.header` rule
comes later so it wins — but confirm that after P1's reshuffling. If both apply to the same element
only one takes effect (they are the same property), so there is no stacking here. The stacking
problem is *nested* blurred elements: a `.glass-panel` dialog inside a blurred backdrop. Check
`.history-dialog` (which has `.glass-panel`) inside `.history-dialog-backdrop` (which has
`blur(4px)`) — that **is** a nested pair and it is the one to watch. If profiling shows it matters,
remove `backdrop-filter` from `.history-dialog` and give it a solid background instead; the backdrop
already provides the frosted context.

### P2.6 — Replace `transition: all`

28 occurrences (see the table in the audit). `transition: all` makes the browser watch every
animatable property, and on `.chip` it includes `backdrop-filter` — meaning a hover re-resolves a
12px blur through an easing curve.

Mechanical, zero visual risk. For each, list only the properties that actually change:

```css
/* Was `transition: all 0.25s ...`.  [M14 P2.6]
   `all` makes the browser watch every animatable property. On .chip that
   includes backdrop-filter, so a hover animated a 12px blur radius. */
.chip {
    transition: background var(--transition),
                border-color var(--transition),
                transform var(--transition),
                box-shadow var(--transition);
}
```

Full list to convert, with the properties each rule's `:hover`/`:focus`/state variants actually
change — **derive this per rule by reading the sibling state rules; do not guess:**

`.status-badge` (351) · `.scroll-fab` (477) · `.chip` (607) · `.message.assistant .msg-content` (709) ·
`.message.user .msg-content` (721) · `.bg-task-card` (791) · `.cam-panel-vision-label` (1006) ·
`.cam-panel-btn` (1026) · `.action-btn` (1151) · `.send-btn` (1172) · `.activity-close` (1349) ·
`.search-results-close` (1522) · `.orb-dash-tab` (1862) · `.orb-dash-reset` (1994) ·
`.jarvis-panel-btn` (3440) · `.reminder-card` (3522) · `.reminder-action-btn` (3586) ·
`.notes-tab` (3637) · `.note-card` (3683) · `.note-card-btn` (3725) · `.todo-list-card` (3765) ·
`.todo-checkbox` (3820) · `.todo-item-text` (3840) · `.todo-item-delete` (3857) ·
`.todo-add-btn` (3910) · `.reminder-toast-btn` (4013)

*(The two duplicates at 2489 and 2668 disappear with P1.)*

In practice almost all of them need exactly:
`transition: background var(--transition), border-color var(--transition), color var(--transition), transform var(--transition);`
Add `box-shadow` and/or `opacity` where a state rule changes them.

### P2.7 — Stop the always-running decorative animations that nobody notices

| Animation | Selector | Line | Decision |
|---|---|---|---|
| `logoSheen 6s linear infinite` | `.logo` | 2089 (moves in P1.6) | **Delete.** It animates `background-position` on a `background-clip: text` element that is **always visible in the header**, forcing a text repaint forever. The sheen is invisible in normal use. |
| `gradientShift 6s infinite` | `.welcome-title` | 572, and again at 526 | **Keep, but scope it.** It only exists on the welcome screen, which is destroyed on the first message (`hideWelcome()`, script.js:2071). It is genuinely a nice touch. **However** it is declared twice (526 sets `fadeInUp` + `gradientShift`, 572 sets `gradientShift`) — consolidate to one declaration. |
| `welcomeFloat 4s infinite` | `.welcome-icon` | 2073 (moves in P1.6) | **Keep.** Welcome screen only, removed after first message. |
| `pulse-dot 2s infinite` | `.status-dot` | 366 | **Keep.** It is a live-status indicator; that is information. |
| `pulse-dot 1.5s infinite` | `.cam-panel-dot` | 982 | **Keep.** Only while the camera panel is open. |
| `pulse-dot 2s infinite` | `.search-results-title::before` | 1508 | **Keep.** Only while the widget is open. |
| `sendGlow 2s infinite` (animates `box-shadow`) | `.send-btn.has-text` | 1183 | **Keep but cheapen.** Animating `box-shadow` re-rasterises. Convert to an `opacity` animation on a pseudo-element that holds a static glow — or simply reduce to a 2-keyframe `opacity` pulse on `filter: brightness()`. **Simplest acceptable change: keep it, it only runs while the input has text and the button is 40px.** Note it and move on. |
| `sendBtnPulse 1.2s infinite` | `.send-btn:disabled` | 1196 | **Keep.** Runs only while streaming; it signals "busy". |
| `micPulse` / `ttsPulse 1.5s infinite` (animate `box-shadow`) | `.mic-btn.listening`, `.tts-btn.tts-speaking` | 1212, 1227 | **Keep.** Both are state indicators and only run during their state. |
| `spin 0.8s linear infinite` | `.bg-task-spinner` | 816 | **Keep.** |
| `blink 0.8s step-end infinite` | `.stream-cursor` | 751 | **Keep.** |
| `historyShimmer 1.4s` | `.history-skeleton` | 4363 | **Keep.** Loading state only. |
| `orbPulse` × 6 states | `#orb-container.orb-*` | 140–190 | **Keep, but note:** these `transform: scale` an element carrying drop-shadows. P2.4's single-drop-shadow change is what makes them affordable. |

**Net effect of P2.7:** delete exactly one animation (`logoSheen`) and consolidate one duplicate
(`gradientShift`). Everything else earns its keep because it is state-scoped. **Resist the urge to
strip more** — the user likes the aesthetic and these do not run at idle.

### P2.8 — Add `color-scheme` and a paint hint

In `index.html` `<head>`, after the existing `theme-color`:
```html
    <meta name="color-scheme" content="dark">
```
And in `style.css` `:root`:
```css
    color-scheme: dark;   /* native scrollbars, form controls and the
                             pre-paint canvas match the app instead of
                             flashing white on first paint */
```
This removes the white flash before CSS applies and makes native UI (date pickers, the
`<input type="search">` clear button, `<input type="color">` in the orb dashboard) render dark
without custom CSS.

⚠ **TRAP:** `color-scheme: dark` changes the default rendering of `<input type="search">`'s native
clear affordance and of `<input type="color">` (orb dashboard, `#orb-s-glowcolor`). Re-check
screenshot `10-orb-dashboard.png` after this change.

### Verify
- **Re-run every P0.3 measurement** and put before/after in `docs/UI_BASELINE.md`. Required results:
  - Idle CPU, tab focused: **< 40% of baseline**
  - Idle CPU, **tab hidden: ≈ 0%** (the orb loop is stopped, star field is static, `logoSheen` is
    gone; the 2s activity timer still fires — P7 fixes that, so a small residue is expected here)
  - Idle CPU with settings panel open (orb occluded): **≈ 0%** for the GPU process
  - `style.css` size unchanged or smaller vs post-P1
- With `?perf=1`: idle `fps` should read `0` when the tab is hidden and you return to it *after* the
  reading, and `long 0` during a streamed reply is **not** expected yet — P3 fixes streaming.
- **Reduced-motion emulation:** nothing animates except the stream cursor and task spinner; no
  element is invisible; the orb is a still image but still changes colour per state (send a message
  and watch it go purple → amber → purple).
- Toggle reduced-motion **at runtime** with the app open — the orb must stop/start without a reload
  and without snapping its rotation (this is the `lastTs` trap).
- Switch to another app for 60 seconds, come back: the orb resumes smoothly, does **not** jump, and
  its state matches whatever JARVIS is doing.
- Open the orb dashboard: the orb keeps animating (it must **not** be occlusion-paused) and sliders
  still give live preview.
- Open the settings panel: the orb pauses. Close it: resumes.
- Resize the window while the orb runs: no distortion, no aspect stretch (proves the `renderScale`
  change is correct).
- Set `window.devicePixelRatio` high via DevTools device emulation: the orb still looks the same
  size and shape.
- **Re-take screenshots 01, 02, 11, 19** and compare. Expect: star pattern differs (accepted, P2.2),
  header slightly less blurred (accepted), camera panel more opaque (accepted), orb glow slightly
  tighter (accepted). Nothing else.
- `node --check web/orb.js && node --check web/script.js`

### Rollback
`git checkout HEAD~1 -- web/style.css web/orb.js web/script.js web/index.html`

---
## P3 — STREAMING CORRECTNESS: CURSOR LEAK, ERROR RECOVERY, STOP BUTTON, IDLE TIMEOUT

**Goal:** A stream that dies mid-reply must degrade honestly and recoverably, and the user must be
able to stop a reply.
**Why:** This is the most visible correctness bug in the app. Today, a mid-stream network drop:

1. leaves the blinking `.stream-cursor` in the DOM **forever** — `if (cursorEl) cursorEl.remove()`
   sits on the happy path only (`script.js:2342`, and again at `1408`), never in `catch`/`finally`.
   `cursorEl` is also `let`-scoped *inside* the `try`, so `finally` cannot see it even if you wanted it to.
2. appends a **second assistant bubble** under the truncated one containing the raw browser string
   `Failed to fetch` (`script.js:2364` — `msg = err.message.slice(0,97)`). The user sees Jarvis
   apparently say "Failed to fetch".
3. leaves **queued TTS audio still playing** over a dead stream — `ttsPlayer.reset()` is not called
   in `catch` or `finally`.
4. offers **no retry**, and there is **no stop button at all**. The `AbortController` exists but is
   only reachable from the PTT handler (`pttStreamController`, `script.js:2229`).
5. hangs for **300 seconds** on a silent death, because `setTimeout(() => controller.abort(), 300000)`
   (`script.js:2225`, `1380`) is an **absolute** timeout. A stream that stops producing bytes at
   second 3 shows a live cursor until minute 5.

There is a deeper honesty problem too, and it matters more than the cosmetics. `CLAUDE.md` rule #1
says tools are the only truth and the UI must never imply something happened when it did not. A
**truncated reply that looks complete** breaks that rule at the presentation layer: the user cannot
tell "Jarvis finished" from "the connection died halfway". Fixing this is not polish.

### Files
- edit: `web/script.js`
- edit: `web/style.css`
- edit: `web/index.html`

### Design decisions (make these exactly; do not improvise)

**D1 — One assistant bubble per turn, always.** A failure never adds a second `.message`. The
existing bubble gets a `.msg-error` footer appended inside its own `.msg-content`. Rationale: two
bubbles reads as "Jarvis said two things", which is a lie.

**D2 — Distinguish the three failure classes**, because the right user action differs:

| Class | Detection | Bubble shows | Retry offered |
|---|---|---|---|
| **Never started** | `fetch` rejected, or `!res.ok`, before any chunk | Replace the placeholder entirely with the error | **Yes** — resend the same text |
| **Died mid-stream** | reader threw, or idle timeout fired, after ≥1 chunk | Keep the partial text, mark it truncated, append error footer | **Yes** — but labelled "Regenerate", and it must **not** re-send silently (see D4) |
| **Stopped by user** | user clicked Stop | Keep the partial text, mark it stopped (neutral styling, not red) | No retry button; the input is simply re-enabled |

**D3 — The truncation marker is text, not just colour.** A `⌁ Response incomplete — connection
lost` line inside the bubble, plus `.msg-content.truncated` for styling. Colour alone fails
WCAG 1.4.1 and, more practically, fails a glance.

**D4 — Retry after a mid-stream death re-sends the original user text as a new turn.** Do **not**
attempt to resume: the backend has no resume endpoint, `sessions[sid][-1]` has already accumulated a
partial assistant message server-side, and inventing a resume protocol is out of scope (§2.1 rule 9).
The retry button therefore says **Regenerate** and calls `sendMessage(originalText)`. The partial
bubble stays visible above it as history. **Do not** delete the partial — deleting evidence of a
failure is the same category of dishonesty as D1.

**D5 — Idle timeout, not absolute.** Reset the timer on every `reader.read()` that returns bytes.
Two constants:

```js
// A stream that has produced no bytes for this long is dead. This replaces the
// old 300s ABSOLUTE timeout, which meant a stream that died at second 3 left a
// live cursor on screen until minute 5.  [M14 P3]
const STREAM_IDLE_TIMEOUT_MS  = 45000;   // gap between chunks
const STREAM_TOTAL_TIMEOUT_MS = 600000;  // absolute ceiling, 10 min
```

⚠ **TRAP:** 45s must be **larger than the slowest legitimate gap**. The backend can legitimately be
silent for a while: `AGENT_MAX_STEPS=16` × `AGENT_STEP_TIMEOUT=30` means a long agent turn can run
minutes — *but* it emits an `activity` SSE event per tool call, and **activity events count as
bytes**. So the idle timer resets on *any* SSE frame, not only on `chunk` frames. Verify this by
running a genuinely long multi-step task ("open notepad, type a haiku, save it to my desktop") and
confirming no spurious timeout. If you see one, the reset is in the wrong place — it must be right
after `sseBuffer += decoder.decode(...)`, not inside the `if ('chunk' in data)` branch.

Keep the absolute ceiling as a backstop against a server that streams keep-alives forever.

**D6 — The Stop button replaces the Send button while streaming.** Do not add a 9th control. The
send button already goes `disabled` during streaming (`script.js:2210`) and already has a
`sendBtnPulse` animation for that state — so the affordance slot exists and is currently wasted on a
dead button.

### Steps

**P3.1 — Hoist stream-teardown state out of the `try` block.**

In `sendMessage`, move these declarations **above** `try`:

```js
    let contentEl   = null;   // the assistant bubble for this turn
    let cursorEl    = null;   // hoisted so `finally` can remove it — the whole
                              // point of P3.1.  Previously scoped inside try{},
                              // so a throw orphaned the blinking cursor forever.
    let fullResponse = '';
    let sawAnyChunk = false;
    let stopped     = false;  // true only when the user clicked Stop
```

Then in `finally`, before the existing body:

```js
    } finally {
        // Teardown that MUST happen on every path, including throws.  [M14 P3.1]
        if (cursorEl) { try { cursorEl.remove(); } catch (_) {} cursorEl = null; }
        clearTimeout(timeoutId);
        clearTimeout(idleTimeoutId);
        ...existing...
    }
```

⚠ **TRAP:** `sendMessageWithImage` has the identical bug at `1408`. **Do not fix it twice.** Fix
`sendMessage` fully here, then P5 deletes `sendMessageWithImage` and routes it through the fixed
path. If you fix both now you will merge two diverged fixes in P5.

**P3.2 — Replace the absolute timeout with idle + absolute.**

```js
    let timeoutId = null;      // absolute ceiling
    let idleTimeoutId = null;  // resets on every SSE frame
    let timedOutIdle = false;  // distinguishes idle-abort from user-abort in catch

    const armIdleTimer = () => {
        clearTimeout(idleTimeoutId);
        idleTimeoutId = setTimeout(() => {
            timedOutIdle = true;
            controller.abort();
        }, STREAM_IDLE_TIMEOUT_MS);
    };
```

Arm it immediately before `await fetch(...)`, and re-arm inside the read loop:

```js
        while (!streamDone) {
            const { done, value } = await reader.read();
            if (done) break;
            armIdleTimer();          // ANY bytes = alive. Activity events count,
                                     // which is why this is here and not inside
                                     // the `chunk` branch.  [M14 P3.2 / D5]
            sseBuffer += decoder.decode(value, { stream: true });
```

⚠ **TRAP:** `controller.abort()` produces `err.name === 'AbortError'` for **three** different causes
now: user Stop, PTT interrupt, and idle timeout. The existing `catch` already special-cases
`isRecording` for PTT. You must now discriminate all three with the explicit `stopped` and
`timedOutIdle` flags — **never** by inspecting `err.message`.

**P3.3 — Rewrite the `catch` block.**

```js
    } catch (err) {
        removeTypingIndicator();

        // Queued TTS must die with the stream. Previously it kept speaking over
        // a dead connection, so Jarvis narrated a reply the user could not see.
        if (ttsPlayer) { try { ttsPlayer.reset(); } catch (_) {} }
        if (preStarterPlayer?.audio) {
            try { preStarterPlayer.audio.pause(); } catch (_) {}
        }

        const aborted = err.name === 'AbortError';

        if (aborted && stopped) {
            // D2 class 3 — user asked for this. Neutral, no error, no retry.
            markStreamIncomplete(contentEl, 'stopped');
            return;                       // `finally` still runs
        }
        if (aborted && isRecording) {
            // PTT interrupt: user is talking over Jarvis. Stay silent (existing
            // behaviour) but still close the bubble honestly.
            markStreamIncomplete(contentEl, 'stopped');
            return;
        }

        const reason = aborted && timedOutIdle
            ? 'No response for 45 seconds — the connection appears to have dropped.'
            : friendlyStreamError(err);

        if (sawAnyChunk) {
            // D2 class 2 — partial reply survives, clearly marked.
            markStreamIncomplete(contentEl, 'lost');
            appendStreamError(contentEl, reason, { retryText: lastUserText, mode: 'regenerate' });
        } else {
            // D2 class 1 — nothing arrived. The placeholder becomes the error.
            if (contentEl) {
                contentEl.textContent = '';
                appendStreamError(contentEl, reason, { retryText: lastUserText, mode: 'retry' });
            } else {
                const el = addMessage('assistant', '');
                appendStreamError(el, reason, { retryText: lastUserText, mode: 'retry' });
            }
        }
        showToast(reason, 6000);
    }
```

You need `lastUserText` — capture `text` into a `const lastUserText = text;` right after the
`messageInput.value = ''` line, because `text` is reassigned in the camera path above.

**P3.4 — Add the three new helpers.** Put them next to `addCorrectionMessage` (~`script.js:2120`),
since they are the same category of thing: telling the user the truth about a failure.

```js
/* ═══════════════════════════════════════════════════════════════════
   Stream failure presentation.  M14 P3.
   ───────────────────────────────────────────────────────────────────
   Rule: ONE assistant bubble per turn, always. A failure never adds a
   second .message — two bubbles reads as "Jarvis said two things",
   which is a lie about what happened.

   And a truncated reply must never look complete. CLAUDE.md rule #1
   says the UI must not imply something happened when it did not; a
   reply that got cut off at 60% and looks finished breaks that at the
   presentation layer.
   ═══════════════════════════════════════════════════════════════════ */

const STREAM_INCOMPLETE_LABEL = {
    lost:    '\u2301 Response incomplete \u2014 connection lost.',
    stopped: '\u25A0 Stopped.',
};

function markStreamIncomplete(contentEl, kind) {
    if (!contentEl) return;
    // Guard: a retry path could call this twice on the same bubble.
    if (contentEl.querySelector('.msg-incomplete')) return;
    contentEl.classList.add('truncated', kind === 'stopped' ? 'stopped' : 'lost');
    const tag = document.createElement('div');
    tag.className = 'msg-incomplete' + (kind === 'stopped' ? ' neutral' : '');
    // Text, not colour alone — WCAG 1.4.1, and it also has to survive a glance.
    tag.textContent = STREAM_INCOMPLETE_LABEL[kind] || STREAM_INCOMPLETE_LABEL.lost;
    contentEl.appendChild(tag);
}

function appendStreamError(contentEl, message, opts = {}) {
    if (!contentEl) return;
    const wrap = document.createElement('div');
    wrap.className = 'msg-error';
    wrap.setAttribute('role', 'status');   // announced, but not an alert-interrupt

    const text = document.createElement('span');
    text.className = 'msg-error-text';
    text.textContent = message;
    wrap.appendChild(text);

    if (opts.retryText) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'msg-error-retry';
        btn.textContent = opts.mode === 'regenerate' ? 'Regenerate' : 'Retry';
        // D4: no resume protocol exists. This sends a NEW turn with the same
        // text. The partial reply above stays as evidence of the failure.
        btn.addEventListener('click', () => {
            btn.disabled = true;
            sendMessage(opts.retryText);
        }, { once: true });
        wrap.appendChild(btn);
    }
    contentEl.appendChild(wrap);
    scrollToBottom();
}

// Never show the user a raw browser string. "Failed to fetch" is not a sentence
// a personal assistant says.
function friendlyStreamError(err) {
    const raw = (err && err.message) || '';
    if (/\b503\b/.test(raw))  return 'Jarvis is starting up or briefly unavailable. Try again in a moment.';
    if (/\b429\b/.test(raw))  return 'Rate limit reached. Wait a few seconds and try again.';
    if (/\b5\d\d\b/.test(raw)) return 'The server hit an error handling that. Try again.';
    if (/\b40[13]\b/.test(raw)) return 'The server refused that request.';
    if (/Failed to fetch|NetworkError|Load failed/i.test(raw))
        return 'Lost connection to Jarvis. Check the server is still running.';
    if (/No response body/i.test(raw)) return 'The server accepted the request but sent nothing back.';
    // Fall back to the server's own detail if it looks like prose, not a stack.
    if (raw && raw.length < 160 && !/^[A-Z]\w*Error/.test(raw)) return raw;
    return 'Something went wrong. Please try again.';
}
```

⚠ **TRAP:** `role="status"` on `.msg-error`, **not** `role="alert"`. `alert` interrupts the screen
reader mid-sentence, and the reply text is already being announced. Also note the existing
`aria-live` toast container gets the same message via `showToast` — that is one announcement from the
polite toast region and one from `role="status"`. If testing with a real screen reader shows a
double-announcement, drop the `role` from `.msg-error` and keep the toast. Record which you chose.

**P3.5 — Stop button.**

`index.html` — inside `.input-actions`, immediately **after** the send button:
```html
                    <!-- Occupies the same slot as send-btn; exactly one of the two
                         is visible. See toggleSendStop() in script.js. [M14 P3.5] -->
                    <button class="action-btn stop-btn" id="stop-btn" title="Stop generating"
                            aria-label="Stop generating" hidden>
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
                            <rect x="6" y="6" width="12" height="12" rx="2"/>
                        </svg>
                    </button>
```

`script.js`:
```js
const stopBtn = document.getElementById('stop-btn');

// Send and Stop share one slot. The old code just disabled Send while streaming,
// which wasted the only affordance the user needed at that moment.  [M14 P3.5]
function toggleSendStop(streaming) {
    if (sendBtn) sendBtn.hidden = !!streaming;
    if (stopBtn) stopBtn.hidden = !streaming;
}

if (stopBtn) {
    stopBtn.addEventListener('click', () => {
        if (!isStreaming || !pttStreamController) return;
        stopRequestedByUser = true;   // module-scope; read by sendMessage's catch
        try { pttStreamController.abort(); } catch (_) {}
        if (ttsPlayer) { try { ttsPlayer.reset(); } catch (_) {} }
    });
}
```

Wire `stopRequestedByUser` → the local `stopped` flag at the top of `sendMessage`
(`stopRequestedByUser = false;` on entry, `stopped = stopRequestedByUser;` inside the abort branch).

⚠ **TRAP:** `pttStreamController` is set to `null` in `finally`. A Stop click that lands in the same
tick as stream completion will hit `null` — hence the `!pttStreamController` guard. Do **not** solve
this by keeping the controller alive after the stream; that reintroduces a stale-abort hazard.

⚠ **TRAP:** `messageInput.disabled = true` during streaming means Escape does not reach the textarea.
Bind Escape on `document` instead, and only when `isStreaming`:
```js
// Escape stops generation. Bound on document because the textarea is disabled
// while streaming, so it cannot receive the keydown.  [M14 P3.5]
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && isStreaming && !anyDialogOpen()) { stopBtn?.click(); }
});
```
`anyDialogOpen()` must return true when the rename/delete dialog is up, or Escape will stop the
stream instead of closing the dialog. Reuse the existing dialog-state check if one exists; otherwise
test `!document.getElementById('history-dialog-backdrop')?.hidden`.

Replace both `sendBtn.disabled = true/false` sites with `toggleSendStop(true/false)`. Keep
`messageInput.disabled` as-is.

**P3.6 — CSS.** Append to the messages section (**after** P1, so you are editing the live copy):

```css
/* ── Stream failure states ──────────────────────────── [M14 P3] ── */

/* Text label, not colour alone (WCAG 1.4.1). */
.msg-incomplete {
    margin-top: 8px;
    font-size: 0.78rem;
    font-weight: 500;
    color: var(--danger);
    letter-spacing: 0.01em;
}
.msg-incomplete.neutral { color: var(--text-dim); }

/* A dashed lower edge signals "this text stops here on purpose" without
   needing to read the label. */
.msg-content.truncated.lost   { border-bottom: 1px dashed rgba(255, 107, 107, 0.35); }
.msg-content.truncated.stopped{ border-bottom: 1px dashed var(--glass-border); }

.msg-error {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    margin-top: 10px;
    padding: 9px 12px;
    border: 1px solid rgba(255, 107, 107, 0.22);
    border-radius: 10px;
    background: rgba(255, 107, 107, 0.07);
    font-size: 0.82rem;
    line-height: 1.5;
}
.msg-error-text { color: #ffb3b3; flex: 1 1 200px; }

.msg-error-retry {
    flex: 0 0 auto;
    padding: 5px 12px;
    border: 1px solid rgba(255, 255, 255, 0.14);
    border-radius: 8px;
    background: rgba(255, 255, 255, 0.06);
    color: var(--text);
    font: inherit;
    font-size: 0.78rem;
    cursor: pointer;
    /* Explicit property list, never `all` — see P2.6. */
    transition: background var(--transition), border-color var(--transition);
}
.msg-error-retry:hover:not(:disabled) {
    background: rgba(255, 255, 255, 0.11);
    border-color: rgba(255, 255, 255, 0.22);
}
.msg-error-retry:disabled { opacity: 0.45; cursor: default; }
.msg-error-retry:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

/* Stop button: same footprint as .send-btn so the slot does not reflow when
   they swap. Deliberately NOT accent-coloured — stopping is not the primary
   action, it is an escape hatch. */
.stop-btn { color: var(--danger); }
.stop-btn:hover { background: rgba(255, 107, 107, 0.12); }
```

⚠ **TRAP:** `.send-btn` carries 5 `!important` declarations (`style.css:1168–1177`) to beat
`.action-btn`. `.stop-btn` also has `.action-btn`. If `.stop-btn` looks wrong, it is source order,
not specificity — put `.stop-btn` **after** `.send-btn`. Do **not** add more `!important`; P8.1
removes the existing five.

### Verify

Manual, and all of these must be done — this phase is pure failure-path work, so the happy path
passing proves nothing.

1. **Happy path unaffected.** Send a normal message. Reply streams, cursor appears then disappears,
   Send returns, no `.msg-incomplete`, no `.msg-error`.
2. **Kill the server mid-reply.** Ask for a 600-word answer; at ~30% close the uvicorn window.
   Expect: partial text kept, `⌁ Response incomplete — connection lost.`, dashed red underline, one
   error row saying the connection was lost, a **Regenerate** button, **no second bubble**, **no
   blinking cursor**, TTS silent. Restart the server and click Regenerate — a fresh turn runs.
3. **Server down before send.** Stop the server, then send. Expect: one bubble, no partial text,
   "Check the server is still running", a **Retry** button. Never the string `Failed to fetch`.
4. **Idle timeout.** DevTools → Network → throttle to **Offline** mid-stream. Expect a timeout
   message in ~45s, **not** 300s. Confirm with a stopwatch.
5. **Long legitimate task does NOT time out.** Send a genuine multi-step agent task that takes
   > 45s between text chunks (e.g. "open notepad, type a haiku, then save it to my desktop"). It
   must complete. If it aborts, the idle-timer reset is in the wrong place — re-read D5's trap.
6. **Stop button.** Long reply → click Stop. Expect: partial kept, `■ Stopped.`, dim (not red),
   **no** error row, **no** Retry, input immediately usable, TTS silent instantly.
7. **Escape** does the same as Stop. Open the delete dialog while streaming (if reachable) and
   confirm Escape closes the dialog and does **not** stop the stream.
8. **PTT interrupt still silent.** Hold Ctrl+Shift mid-reply. Expect `■ Stopped.` and no error toast.
9. **Camera path.** With the camera open, send an image question and kill the server mid-reply.
   Before P5 this goes through `sendMessageWithImage`, which is **still broken** — confirm it is
   still broken, note it in the phase report, and do not fix it here. P5 removes it.
10. `node --check web/script.js` and 215 tests still pass.

Add a row to `docs/UI_BASELINE.md`: does a failed stream now leave any orphaned DOM? Check with
`document.querySelectorAll('.stream-cursor').length === 0` after test 2.

### Rollback
`git checkout HEAD~1 -- web/script.js web/style.css web/index.html`

---

## P4 — XSS AND UNSAFE-`innerHTML` FIXES

**Goal:** Remove every path where server-, LLM-, or user-controlled text reaches `innerHTML`
unescaped.
**Why:** There is a real, reachable hole. `buildReminderCard()` (`script.js:2566`) interpolates
`r.recurrence` **raw**:

```js
const recurrenceBadge = r.recurrence
    ? `<span class="reminder-card-recurrence">🔁 ${r.recurrence}</span>`   // ← RAW
    : '';
```

Every sibling field uses `escHtml()`. `recurrence` does not. And reminders are created **by the LLM
from the user's speech** (`set_reminder`, `app/services/agent/tools/reminder_tools.py`) and persisted
to `reminders.db`, so `recurrence` is an LLM-authored string that round-trips through storage and
lands in `innerHTML`. That is a stored-injection path.

This is a single-user local app with no auth, so the *severity* is low — the attacker would largely
be the user. But `CLAUDE.md` §14 rule 12 already commits the project to not leaking data, and the
frontend runs same-origin with an API that can **delete files, send email, and shut down the
machine**. A single injected `<img src=x onerror="fetch('/chat/jarvis/stream',{method:'POST',...})">`
inherits all 95 tools. Treat it as real.

⚠ Note the reason this is a *hole* and not just a style problem: `escHtml()` **exists** and is used
on the fields next to it. This is an omission, not a design gap. Which means it will happen again
unless the pattern changes — hence P4.4.

### Files
- edit: `web/script.js`
- new: `web/js/dom.js` (a 40-line helper; the first file in the new `web/js/` folder besides `devtools.js`)

### Steps

**P4.1 — Fix the immediate hole.**

```js
    const recurrenceBadge = r.recurrence
        // escHtml: `recurrence` is LLM-authored (set_reminder parses user speech)
        // and round-trips through reminders.db, so it is stored untrusted input
        // reaching innerHTML. Every sibling field here was already escaped; this
        // one was missed.  [M14 P4.1]
        ? `<span class="reminder-card-recurrence">${ICON.repeat} ${escHtml(r.recurrence)}</span>`
        : '';
```
(`ICON.repeat` comes from P10.4; until then keep the emoji.)

**P4.2 — Audit and fix every remaining raw interpolation.** Find them all:

```bash
# Any ${...} inside a template literal that is NOT wrapped in escHtml/escapeHtml.
grep -n 'innerHTML' web/script.js
grep -nE '\$\{(?!esc)' web/script.js        # use PowerShell Select-String if grep lacks -P
```

Verified inventory. Every `id` below is numeric server-side, so these are **defence in depth, not
live holes** — but "it's a number today" is exactly the assumption that breaks later:

| Site | Line | Raw value | Action |
|---|---|---|---|
| `buildReminderCard` | 2566 | **`r.recurrence`** | **LIVE HOLE** — escape (P4.1) |
| `buildReminderCard` | ×3 | `r.id` → `data-reminder-id` | Use `dataset` after `createElement`, or `escAttr()` |
| `renderNotes` | ~2680 | `n.id` → `data-note-id`, `data-note-delete` | same |
| `renderNotes` | ~2680 | `timeStr` (from `toLocaleDateString`) | Locale-generated, but escape anyway — it is interpolated raw |
| `renderTodos` | ~2740 | `item.id`, `lst.id`, `item.done` | same |
| `renderTodos` | ~2760 | `` `[data-todo-add-input="${listId}"]` `` | **Not HTML — a CSS selector.** A non-numeric id here is a selector-injection / crash, not XSS. Use `CSS.escape(listId)` |
| `showReminderToast` | ~2930 | `data.id` in `tag:` | Notification API, not HTML. Fine. |
| `renderSearchResults` | grep it | verify | Search results are **Serper API output** — third-party, fully untrusted. **Check this one first.** |
| `appendActivity` | grep it | verify | Activity rows carry tool names and observations, i.e. LLM output |
| `addMessage` | 2093 | `AVATAR_ICON_*` → `innerHTML` | **Safe** — module constants, no interpolation. Leave. Add a comment saying why so a future audit does not churn on it. |

⚠ **TRAP:** `renderSearchResults` and `appendActivity` were not in the original survey and I have not
line-verified their internals. **Read both before assuming they are clean.** Serper output (titles,
snippets, URLs from arbitrary websites) is the single most untrusted string in the whole app.

**P4.3 — For the toast and card builders, stop using `innerHTML` at all.**

`showReminderToast` (`~2900`) builds its whole body with `innerHTML` from `data.title` and
`data.description` — both LLM-authored. `escHtml` is applied there today, so it is not currently
exploitable, but the pattern is one edit away from being a hole again. Convert to `createElement` +
`textContent`, exactly as `addMessage` already does.

Same for `buildReminderCard`, `renderNotes`, `renderTodos`. This is mechanical and it is the real fix.

**P4.4 — Add `web/js/dom.js` so the safe path is the easy path.**

```js
/* ═══════════════════════════════════════════════════════════════════
   Safe DOM construction.  M14 P4.
   ───────────────────────────────────────────────────────────────────
   Why this file exists: escHtml() already existed and was already used
   on the fields NEXT TO the one that was vulnerable. Relying on every
   future edit to remember one function call is how that hole appeared.
   `el()` makes the safe path shorter to type than the unsafe one.

   Rule (ground rules §2.1 #7): no string containing server, LLM, or
   user data ever goes to innerHTML. Not "escaped first" — not at all.
   ═══════════════════════════════════════════════════════════════════ */

/** Build an element. `text` goes through textContent; attrs are set with
 *  setAttribute, so neither can inject markup.
 *  el('div', {class: 'x', dataset: {id: 4}}, 'hello')                        */
export function el(tag, attrs = null, ...children) {
    const node = document.createElement(tag);
    if (attrs) {
        for (const [k, v] of Object.entries(attrs)) {
            if (v === null || v === undefined || v === false) continue;
            if (k === 'dataset') {
                for (const [dk, dv] of Object.entries(v)) node.dataset[dk] = String(dv);
            } else if (k === 'class') {
                node.className = v;
            } else if (k === 'text') {
                node.textContent = String(v);
            } else if (k.startsWith('on') && typeof v === 'function') {
                node.addEventListener(k.slice(2).toLowerCase(), v);
            } else if (v === true) {
                node.setAttribute(k, '');
            } else {
                node.setAttribute(k, String(v));
            }
        }
    }
    for (const c of children) {
        if (c === null || c === undefined || c === false) continue;
        node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
}

/** Inline SVG icon. This is the ONLY sanctioned innerHTML in the app, and it
 *  is safe because `path` is always a literal from web/js/icons.js — never
 *  data. If you ever pass a variable here, you have broken the rule. */
export function icon(path, size = 16, extraClass = '') {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('width', size);
    svg.setAttribute('height', size);
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '2');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('aria-hidden', 'true');   // decorative; label the button
    if (extraClass) svg.setAttribute('class', extraClass);
    svg.innerHTML = path;
    return svg;
}

/** Replace all children in one shot. Cheaper than innerHTML='' + appends,
 *  and it cannot be handed a string. */
export function replaceChildren(parent, ...nodes) {
    parent.replaceChildren(...nodes.filter(Boolean));
}
```

⚠ **TRAP:** `script.js` is a **classic** script until P9. It cannot `import`. Two options — pick the
first:

- **(chosen)** Until P9, keep `dom.js` as the module source of truth and add a tiny classic-script
  shim at the top of `script.js` that defines the same `el()` locally, with a comment pointing at
  `dom.js` and a `// P9: delete this shim, import from ./js/dom.js` marker. Duplicating 40 lines for
  one phase is cheaper than reordering P9 before P4 — and P4 is a security fix that should not wait
  behind the riskiest phase in the plan.
- (rejected) Convert `script.js` to a module now. That is P9 and it is high-risk; do not smuggle it in.

**P4.5 — Harden `handleActions` image URLs.**

`script.js:1202` does `img.src = url` with **no scheme check**, while the sibling link path at
`1164` (`safeOpen`) *does* allowlist `http:`/`https:`. Inconsistent, and the image path is the one
that loads automatically without a click. `actions.images` comes from `generate_image` (Pollinations
URLs) — but the allowlist should not depend on that staying true.

```js
        actions.images.forEach(url => {
            // Same allowlist as safeOpen() above. This path had none, even
            // though it is the one that loads WITHOUT a user click. `data:` and
            // `blob:` are excluded on purpose: an inline SVG data URL is a
            // script execution context in some contexts.  [M14 P4.5]
            if (!/^https?:\/\//i.test(url)) return;
            const img = document.createElement('img');
            img.src = url;
            img.referrerPolicy = 'no-referrer';   // don't leak the local URL
            ...
```

**P4.6 — Do NOT add a Content-Security-Policy header.**
Tempting, and it is the textbook answer. But it is a **backend** change (§2.1 rule 9 allows only the
four listed additive edits), the app currently loads Poppins from Google Fonts and inlines styles in
several places, and a CSP that breaks the UI on a machine the user depends on is a worse outcome than
the risk it mitigates. Record it in the phase report as a deliberate deferral with a note that
`style-src 'unsafe-inline'` would be required today, so the CSP would be weak anyway. **If** you want
this later it belongs in its own milestone with its own testing.

### Verify

1. **Reproduce the hole before fixing** — this is the only way to know the fix works:
   ```js
   // In the console, with the reminders panel open. Do NOT use a real API call.
   buildReminderCard({ id: 1, title: 'test', due_at: Date.now()/1000,
                       recurrence: '<img src=x onerror="window.__XSS=1">' });
   ```
   Insert the returned HTML into the panel. Before P4.1, `window.__XSS === 1`. After, it is
   `undefined` and the literal tag text is visible in the badge.
2. Same probe through `showReminderToast({title, description})` and every field in `renderNotes` /
   `renderTodos`.
3. Grep clean: no `${` inside a template literal reaching `innerHTML` without `escHtml`, except the
   two documented icon constants.
4. `handleActions({images:['javascript:alert(1)']}, el)` and `{images:['data:text/html,<script>…']}`
   render nothing and throw nothing.
5. Reminders / notes / todos all still render, and every button still works — the `createElement`
   rewrite is where you will break event binding. Test: done, snooze, delete, note delete, note
   expand, todo toggle, todo delete, list delete, add-item button, add-item Enter key.
6. A to-do list whose title contains `"` and `'` renders correctly and its add-item input still works
   (this is the `CSS.escape` path).
7. `node --check web/script.js`

### Rollback
`git checkout HEAD~1 -- web/script.js` and delete `web/js/dom.js`.

---

## P5 — MERGE `sendMessage` + `sendMessageWithImage`

**Goal:** One SSE consumer in the codebase. Delete ~96 lines.
**Why:** `sendMessage` (2169–2376, 208 lines) and `sendMessageWithImage` (1366–1461, 96 lines) are
~90% identical — two copies of the read loop, the SSE frame dispatch, the cursor handling and the
teardown. They have **already diverged**:

| | `sendMessage` | `sendMessageWithImage` |
|---|---|---|
| Orb state transitions from activity events | yes (8 branches) | **no** — orb never leaves the state it entered with |
| `orb.setState('thinking')` on entry | yes | **no** |
| Search-results rendering | yes | **no** — a vision reply that triggers a search shows nothing |
| Starter-audio handoff on first chunk | yes | **no** |
| `firstChunkReceived` bookkeeping | yes | no |
| Error message | classified, with 503/429 handling | flat `'Something went wrong analyzing the image.'` |
| `pttStreamController` exposure | yes | **no** — PTT cannot interrupt a vision reply |
| P3 fixes | yes | **no** |
| `stopStartupBrief()` | yes | yes |

That table is the argument. The image path is not a variant, it is a **stale fork**. Every fix in
P3 and every feature in P6 would otherwise need writing twice, and the second copy would keep losing.

**Sequencing note:** P3 deliberately fixed only `sendMessage`. That was not laziness — it means this
phase is a pure deletion with one new parameter, rather than a three-way merge of two diverged
copies plus a new fix.

### Files
- edit: `web/script.js`

### Steps

**P5.1 — Widen `sendMessage`'s signature.**

```js
/**
 * The single SSE consumer. Everything that talks to /chat/jarvis/stream comes
 * through here.
 *
 * @param {string} [textOverride]  send this instead of reading the textarea
 * @param {object} [opts]
 * @param {string} [opts.image]    base64 JPEG (no data: prefix) to attach
 * @param {boolean}[opts.silent]   don't clear/echo the textarea (image resend path)
 *
 * There used to be a second copy of this function for the image path. It had
 * drifted badly: no orb state transitions, no search-results rendering, no
 * starter-audio handoff, no PTT interrupt, and a generic error string. Vision
 * replies were a visibly worse experience for no reason other than the fork.
 * Do not fork it again.  [M14 P5]
 */
async function sendMessage(textOverride, opts = {}) {
```

**P5.2 — Take the image from either source.** The existing camera-capture logic (2170–2198) already
resolves an image. Add the explicit override **before** it and skip auto-capture when present:

```js
    // An explicit image (the open_and_capture resend path) wins over auto-capture.
    let imgBase64 = opts.image || null;
    ...
    if (!imgBase64 && camStream && wantsCamera) {
        imgBase64 = await captureFrameAsBase64Safe();
        if (!imgBase64) showToast('Camera frame not ready. Please try again.');
    }
```

⚠ **TRAP:** the old image path appended `CAM_BYPASS_TOKEN` unconditionally; `sendMessage` appends it
only `if (imgBase64)`. Same outcome now that the image is unified — but confirm the token is still
appended in the resend path, or the backend will not route to vision. Grep `CAM_BYPASS_TOKEN` and
check the single remaining site.

⚠ **TRAP:** the old image path did **not** call `startCamera()` and did **not** run
`isCameraQuery(text)`. It was called from a context where the camera was already open and the frame
already captured. With `opts.image` set you must **skip** the camera-open block entirely, or
`handleActions`' `open_and_capture` path will re-open and re-await a camera that is already live —
adding ~1s and possibly a second capture. Guard it: `if (!imgBase64 && (isCameraQuery(text) || visionModeOn) && !camStream)`.

**P5.3 — Update the two call sites.**
- `handleActions` `open_and_capture` (`script.js:1275`): `sendMessageWithImage(resendMsg, frame)` →
  `sendMessage(resendMsg, { image: frame, silent: true })`
- Grep for any others: `grep -n 'sendMessageWithImage' web/script.js`. Expect exactly one call site
  plus the definition.

**P5.4 — Delete `sendMessageWithImage` entirely** (1366–1461).

**P5.5 — Delete `captureFrameAsBase64`** (1301–1318). Zero call sites; `captureFrameAsBase64Safe` is
the live one. Verify with `grep -n 'captureFrameAsBase64\b' web/script.js` — the `\b` matters or you
will match the `Safe` variant.

**P5.6 — Honour `opts.silent`.** The textarea-clearing block (`messageInput.value = ''`,
`autoResizeInput()`, `charCount.textContent = ''`) must be skipped when `silent` is set, because in
the resend path the textarea holds whatever the user is typing next. The `addMessage('user', text)`
call **should still run** — the user needs to see what was asked about the image.

### Verify
1. Normal text message: unchanged.
2. Type a question with the camera panel open: image attaches, vision reply streams, **and the orb
   now transitions** (thinking → working/speaking) where it previously froze. This is the visible
   proof the merge worked.
3. Say "what do you see" with the camera closed: `open_and_capture` fires, camera opens **once**
   (watch the panel — no double flash), frame captures, reply streams.
4. During a vision reply, hold Ctrl+Shift: PTT now interrupts it (**new capability**).
5. Kill the server during a vision reply: full P3 treatment — partial kept, marked incomplete,
   Regenerate offered. This is test 9 from P3, which was expected to fail then and must pass now.
6. Vision Mode checkbox on, send three messages in a row: image attaches each time, textarea clears
   each time.
7. `grep -c 'res.body.getReader' web/script.js` → **1**. If it is 2, the merge is incomplete.
8. `node --check web/script.js`; line count down ~110.

### Rollback
`git checkout HEAD~1 -- web/script.js`

---

## P6 — STREAMING-SAFE MARKDOWN RENDERING

**Goal:** Assistant replies render markdown — bold, italic, inline code, fenced code with a copy
button, headings, lists, links, tables, blockquotes — correctly **while streaming**.
**Why:** This is the single biggest UX gap in the app. `script.js:2319` does
`textSpan.textContent = fullResponse`, so every reply shows raw syntax: `**Sir**`, `` `C:\Users` ``,
`### Summary`, `- item`, and code fences as literal backtick lines. The LLM emits markdown constantly
— Groq/gpt-oss and Gemini both do it by default — so this affects nearly every substantial reply.

`app/services/notes_service.py` already stores a `markdown_body` field, and the notes renderer shows
it as plain text too. So the backend is already markdown-aware and the frontend is not.

### The hard part, stated plainly

Markdown parsers are designed for **complete documents**; a stream delivers a **character sequence**.
Rendering each partial state naively produces broken output: an unclosed `**` swallows the rest of
the reply into bold, a half-typed ``` opens a code block that never closes, a table renders as
garbage until its final pipe arrives, and a `[link` flashes as literal text then reflows.

The established fix is to **repair the unterminated markdown before rendering, on every frame** —
close dangling fences, drop incomplete emphasis/link syntax rather than rendering it half-applied
([Streamdown documents this as a `remend` preprocessing step that closes dangling fences and completes
half-written emphasis and links](https://reactlibs.dev/articles/streamdown/); its
[termination logic walks blocks line-by-line per CommonMark rather than counting markers](https://vercel-streamdown.mintlify.app/advanced/termination)).
*Content was rephrased for compliance with licensing restrictions.*

We implement that idea by hand. **No library** (§2.1 rule 3) — and this is genuinely fine, because we
need a *small* subset, we need it XSS-safe by construction, and a hand-written renderer that builds
DOM nodes is both smaller and safer than `marked` + `DOMPurify` (~45 KB combined).

### Design decisions

**D1 — Block-level diffing, not full re-render.** Re-parsing and replacing the entire message on every
chunk is O(n²) over a reply and destroys text selection, scroll anchoring, and focus. Instead:

- Split `fullResponse` into **top-level blocks** (paragraph, heading, fence, list, table, quote, hr).
- Only the **last** block can still change. Blocks before it are final.
- Re-render only the last block; leave earlier DOM untouched.

This is the difference between a smooth stream and a stuttering one, and it is why P0's frame-time
baseline matters here.

**D2 — Render to DOM nodes, never to an HTML string.** No `innerHTML` anywhere in the renderer
(§2.1 rule 7). Text always lands in `textContent`. This makes XSS **structurally impossible** rather
than escaped-by-convention — which, per P4, is exactly the convention that already failed once.

**D3 — Throttle to one render per animation frame.** Chunks can arrive faster than 60 Hz. Coalesce.

**D4 — Fail soft (§2.2 rule 4).** The whole render is wrapped; on throw, fall back to `textContent`
of the raw markdown. A renderer bug must never lose the reply.

**D5 — User messages stay plain text.** Rendering the user's own input as markdown means a user who
types `*` gets surprising output, and it re-opens an injection surface for zero benefit.

**D6 — Correction bubbles stay plain text.** They are short backend-generated sentences.

**D7 — Only close fences during streaming; never rewrite the final text.** When `done` arrives,
render the raw `fullResponse` once with no repair, so the last render is faithful.

**D8 — No syntax highlighting.** It needs a grammar per language (a library, or hundreds of lines of
regex) and burns CPU per frame on the largest blocks. Monospace + a copy button covers the actual
need. Explicitly out of scope; do not add it.

### Files
- new: `web/js/markdown.js`
- edit: `web/script.js`
- edit: `web/style.css`

### Steps

**P6.1 — Write `web/js/markdown.js`.** Full specification of the supported subset — implement
**exactly** this, no more:

| Syntax | Renders as | Notes |
|---|---|---|
| `# ` … `###### ` | `<h1>`…`<h6>` | ATX only. Start of line. No Setext. |
| ` ```lang ` fence | `<pre><code>` + header with language + Copy button | `~~~` also accepted |
| indented 4-space block | `<pre><code>` | Only when not inside a list |
| `- ` `* ` `+ ` | `<ul><li>` | Nesting to **2** levels. Deeper is flattened — document it. |
| `1. ` | `<ol><li>` | `start` honoured from the first number |
| `> ` | `<blockquote>` | One level. Nested quotes flatten. |
| `---` `***` `___` | `<hr>` | Needs a blank line before it, else it is Setext-ish ambiguity — treat as `<hr>` anyway, we do not support Setext |
| `\|a\|b\|` + `\|---\|---\|` | `<table>` | Header row required. Alignment row parsed for `:` alignment. |
| `**x**` `__x__` | `<strong>` | |
| `*x*` `_x_` | `<em>` | `_` inside a word does **not** emphasise (`snake_case_name` stays literal) |
| `~~x~~` | `<del>` | |
| `` `x` `` | `<code>` | Wins over all other inline syntax; content is never further parsed |
| `[t](url)` | `<a>` | **`http:`/`https:`/`mailto:` only.** Anything else renders as literal text. `target="_blank" rel="noopener noreferrer"` |
| bare `https://…` | `<a>` | Autolink. Trailing `.,;:!?)` excluded from the URL. |
| `\*` `` \` `` etc. | literal | Backslash escapes |

**Not supported, on purpose:** reference links, footnotes, HTML passthrough (raw HTML renders as
literal text — that is a feature), images (`![]()` → renders as a link; `handleActions` owns real
images), task-list checkboxes, definition lists, math.

Skeleton — the structure is prescriptive, the internals of the inline parser are yours:

```js
/* ═══════════════════════════════════════════════════════════════════
   Streaming-safe markdown → DOM.  M14 P6.
   ───────────────────────────────────────────────────────────────────
   Two problems this solves, both specific to streaming:

   1. A markdown parser expects a finished document. A stream hands you
      a prefix. Naively parsing every prefix means an unclosed ** turns
      the rest of the reply bold, and a half-typed ``` opens a code
      block that never closes.  → repairForStream() closes what is open
      and drops what is half-written, EVERY frame.

   2. Re-rendering the whole message per chunk is O(n^2) and destroys
      text selection and scroll anchoring.  → render() diffs at BLOCK
      level and only re-renders the last block, since only the last
      block can still change.

   Zero dependencies and zero innerHTML. Text always lands in
   textContent, so injection is structurally impossible rather than
   escaped-by-convention — see P4 for why that distinction earned its
   place in this codebase.
   ═══════════════════════════════════════════════════════════════════ */

const SAFE_SCHEME = /^(https?:|mailto:)/i;

/** Split into top-level blocks. Returns [{type, raw, ...}]. A fence swallows
 *  lines until its closer regardless of blank lines — that is what makes
 *  fences the tricky case. */
export function splitBlocks(src) { /* … */ }

/** Close dangling constructs so a partial document renders sanely.
 *  Called on every streaming frame, never on the final one (D7). */
export function repairForStream(src) {
    let out = src;

    // 1. Unclosed fence: count fence lines. Odd → append a closer.
    //    Walk line-by-line and match the OPENING fence's char and length;
    //    ``` does not close ~~~~, and ``` does not close ````.
    //    (Counting occurrences of '```' anywhere is the classic bug — it
    //     matches inline code spans and mid-word backticks.)

    // 2. Trailing inline markers that cannot yet be balanced: strip the
    //    dangling run rather than rendering half-applied emphasis. Order
    //    matters — check ** before *.
    //      "**Sir"      → "Sir"      (not "<strong>Sir")
    //      "*hello"     → "hello"
    //      "`C:\\Use"   → "C:\\Use"
    //      "[Google"    → "[Google"  (literal; a link needs its closer)
    //      "[Google](ht"→ "Google"   (text only, no href yet)

    // 3. A table whose delimiter row has not arrived yet: hold the partial
    //    rows as a plain paragraph so it does not flash as a broken table.

    return out;
}

/** Inline parse → DocumentFragment. Code spans bind tightest and their
 *  contents are never re-parsed. */
export function renderInline(text) { /* … */ }

/** One block → Element. */
export function renderBlock(block) { /* … */ }

/**
 * Incremental render.
 * @param {HTMLElement} host      container owned entirely by this renderer
 * @param {string} src            full markdown so far
 * @param {boolean} streaming     true → repair first; false → render verbatim
 */
export function render(host, src, streaming) {
    try {
        const blocks = splitBlocks(streaming ? repairForStream(src) : src);
        const rendered = host._mdBlocks || (host._mdBlocks = []);

        // Only the last block can still change; everything before it is final.
        // This is the whole performance story of this file. [D1]
        let firstDirty = 0;
        while (firstDirty < rendered.length
               && firstDirty < blocks.length - 1
               && rendered[firstDirty] === blocks[firstDirty].raw) {
            firstDirty++;
        }
        while (host.childNodes.length > firstDirty) host.lastChild.remove();
        rendered.length = firstDirty;
        for (let i = firstDirty; i < blocks.length; i++) {
            host.appendChild(renderBlock(blocks[i]));
            rendered[i] = blocks[i].raw;
        }
    } catch (e) {
        // D4: a renderer bug must never lose the reply.
        console.warn('[markdown] render failed, falling back to plain text', e);
        host.textContent = src;
    }
}
```

⚠ **TRAP (the one that will actually bite you):** counting `'```'` occurrences with
`split('```').length` is wrong. It matches inline code spans, and it cannot tell ` ``` ` from ` ```` `
or `~~~`. You must walk lines and track the *opening* fence's character and run length, closing only
on a line whose fence uses the same char and is at least as long. This is the same conclusion
[Streamdown reached — structural analysis, not marker counting](https://vercel-streamdown.mintlify.app/advanced/termination).

⚠ **TRAP:** `host._mdBlocks` caches by raw block text. If two adjacent blocks have identical raw text
(easy: two `---` rules, or a repeated list item), the prefix comparison still works because it is
positional. But **do not** switch to a `Set` or a keyed map "for speed" — position is the identity here.

⚠ **TRAP:** Windows paths. Jarvis talks about `C:\Users\ayush_lr8ru2y\...` constantly. Your backslash
escape handling must not eat them: `\U` is not a valid markdown escape, so a backslash followed by a
non-punctuation character must stay literal. Test with `C:\Users\Desktop\notes.txt` **outside** a code
span. This will be the first real bug report if you get it wrong.

⚠ **TRAP:** `_` in identifiers. `snake_case_name`, `session_id`, `--accent-secondary`, `data.query_type`
all appear in this app's own replies. Intraword `_` must not emphasise. `*` intraword may.

**P6.2 — Fenced code block: header + copy button.**

```
┌────────────────────────────────┐
│ python                  ⧉ Copy │   ← .md-code-head
├────────────────────────────────┤
│ def hello():                   │   ← <pre><code>
│     print("hi")                │
└────────────────────────────────┘
```
- Language label from the fence info string, lowercased, `[a-z0-9+#-]` only, else omit.
- Copy uses `navigator.clipboard.writeText(code.textContent)`; on failure fall back to a hidden
  `<textarea>` + `document.execCommand('copy')`. Show "Copied" in the button for 1.5s.
- Copy button must **not** appear while that fence is still streaming (it would copy a half-block).
  Only add it when the block is not the last block, **or** when `streaming === false`.
- `<pre>` gets `overflow-x: auto` and **must not** widen the message bubble — see the CSS trap below.

**P6.3 — Wire it into the stream.**

`script.js`, replacing the `textSpan.textContent = fullResponse` site:

```js
                    if ('chunk' in data) {
                        const chunkText = data.chunk || '';
                        ...
                        fullResponse += chunkText;
                        sawAnyChunk = true;
                        scheduleMarkdownRender(contentEl, fullResponse, true);
                        scrollToBottom();
                    }
```

```js
// One render per animation frame. Chunks arrive faster than 60Hz and each
// render touches the DOM, so coalescing is not an optimisation, it is what
// keeps the frame budget.  [M14 P6 / D3]
let _mdFrame = 0;
let _mdPending = null;
function scheduleMarkdownRender(contentEl, src, streaming) {
    _mdPending = { contentEl, src, streaming };
    if (_mdFrame) return;
    _mdFrame = requestAnimationFrame(() => {
        _mdFrame = 0;
        const p = _mdPending; _mdPending = null;
        if (!p) return;
        const host = p.contentEl.querySelector('.msg-stream-text');
        if (host) window.JarvisMarkdown.render(host, p.src, p.streaming);
    });
}
```

After the loop ends, do a **final** verbatim render (D7):
```js
        // Final pass with streaming=false: no repair, so the last thing on
        // screen is exactly what the model said.
        if (_mdFrame) { cancelAnimationFrame(_mdFrame); _mdFrame = 0; }
        const host = contentEl?.querySelector('.msg-stream-text');
        if (host && fullResponse) window.JarvisMarkdown.render(host, fullResponse, false);
```

⚠ **TRAP:** `.msg-stream-text` currently also receives `handleActions` output — no it does not,
`handleActions` appends to `contentEl`, the **parent**. Good. But the markdown renderer **owns**
`.msg-stream-text` completely and clears children it does not recognise. So `.msg-stream-text` must
contain nothing else, ever. `handleActions` appending to `contentEl` is correct; keep it that way and
add a comment saying so.

⚠ **TRAP:** the `stream-cursor` is appended to `contentEl` (parent), not to `.msg-stream-text`. Also
correct. Verify after your change that the cursor still sits visually at the end of the text — with
block-level markdown the last block is a `<p>`, which is `display: block`, so an inline cursor after
it will drop to its own line. Fix in CSS: `.msg-stream-text > :last-child { display: inline; }` is
**wrong** (breaks code blocks). Instead position the cursor absolutely relative to the last block, or
simply accept it on its own line — **or best**: move the cursor *inside* the last block when that
block is a paragraph. Pick one, comment the choice.

⚠ **TRAP:** `script.js` is classic until P9, so it cannot `import`. Load `markdown.js` as a module
that assigns `window.JarvisMarkdown = { render }`, with a `// P9: replace with a real import` marker.
Guard the call site: `if (window.JarvisMarkdown) … else host.textContent = src;` so a load failure
degrades to today's behaviour instead of a blank reply.

**P6.4 — Also render markdown in loaded history.** `selectConversation()` rebuilds bubbles from the
saved transcript. Those must render markdown too, with `streaming = false` — otherwise reopening a
conversation shows raw syntax and the app looks broken in exactly the place a user goes to re-read
something. Find where it calls `addMessage('assistant', …)` and route the assistant branch through
`render(host, text, false)`.

**P6.5 — Also render `markdown_body` in the notes panel.** `renderNotes` (`~2680`) prefers
`n.markdown_body || n.body` and then shows it as plain text. The field is literally named
`markdown_body`. Render the **preview** as plain text (truncated at 120 chars — markdown of a
fragment is meaningless) and the **expanded** view as markdown.

**P6.6 — CSS.** New section. Full spec:

```css
/* ── Markdown in assistant replies ──────────────────── [M14 P6] ── */

/* The renderer owns this element's children entirely. Nothing else may
   append here — handleActions() and the stream cursor attach to the
   parent .msg-content on purpose. */
.msg-stream-text > *:first-child { margin-top: 0; }
.msg-stream-text > *:last-child  { margin-bottom: 0; }

.msg-stream-text p { margin: 0 0 0.7em; }
.msg-stream-text h1,
.msg-stream-text h2,
.msg-stream-text h3,
.msg-stream-text h4,
.msg-stream-text h5,
.msg-stream-text h6 {
    margin: 1.1em 0 0.5em;
    font-weight: 600;
    line-height: 1.3;
}
/* Chat messages are not a document — h1 at document scale looks absurd in a
   bubble. Compress the scale hard. */
.msg-stream-text h1 { font-size: 1.24rem; }
.msg-stream-text h2 { font-size: 1.13rem; }
.msg-stream-text h3 { font-size: 1.04rem; }
.msg-stream-text h4,
.msg-stream-text h5,
.msg-stream-text h6 { font-size: 0.97rem; color: var(--text-dim); }

.msg-stream-text ul,
.msg-stream-text ol { margin: 0 0 0.7em; padding-left: 1.35em; }
.msg-stream-text li { margin: 0.22em 0; }
.msg-stream-text li > ul,
.msg-stream-text li > ol { margin: 0.22em 0 0.1em; }

.msg-stream-text blockquote {
    margin: 0 0 0.7em;
    padding: 0.1em 0 0.1em 0.9em;
    border-left: 2px solid var(--accent);
    color: var(--text-dim);
}

.msg-stream-text hr {
    margin: 1em 0;
    border: 0;
    border-top: 1px solid var(--glass-border);
}

.msg-stream-text code {
    padding: 0.12em 0.4em;
    border-radius: 5px;
    background: rgba(124, 106, 239, 0.14);
    color: #cfc6ff;
    font-family: var(--font-mono);
    font-size: 0.88em;
    /* A long path or token must wrap rather than widen the bubble. */
    overflow-wrap: anywhere;
}

.msg-stream-text a {
    color: var(--accent-secondary);
    text-decoration: underline;
    text-underline-offset: 2px;
    overflow-wrap: anywhere;
}
.msg-stream-text a:hover { color: #7fded6; }

/* ── Fenced code ── */
.md-code {
    margin: 0 0 0.75em;
    border: 1px solid var(--glass-border);
    border-radius: 10px;
    background: rgba(0, 0, 0, 0.34);
    overflow: hidden;              /* clips the children's corners */
    /* Isolate layout/paint: a long code block is the most expensive thing in
       a reply, and this keeps it from invalidating the rest of the thread. */
    contain: content;
}
.md-code-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    padding: 5px 10px;
    border-bottom: 1px solid var(--glass-border);
    background: rgba(255, 255, 255, 0.035);
    font-size: 0.7rem;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--text-dim);
}
.md-code-copy {
    padding: 3px 9px;
    border: 1px solid var(--glass-border);
    border-radius: 6px;
    background: transparent;
    color: var(--text-dim);
    font: inherit;
    font-size: 0.68rem;
    text-transform: none;
    letter-spacing: 0;
    cursor: pointer;
    transition: background var(--transition), color var(--transition);
}
.md-code-copy:hover  { background: rgba(255,255,255,0.08); color: var(--text); }
.md-code-copy.copied { color: var(--success); border-color: rgba(78,205,196,0.4); }
.md-code-copy:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

.md-code pre {
    margin: 0;
    padding: 11px 13px;
    overflow-x: auto;
    /* Do NOT let a wide line stretch the bubble. min-width:0 on the flex
       ancestor is what actually makes this work — see the trap below. */
}
.md-code pre code {
    padding: 0;
    background: none;
    color: #d8d4ee;
    font-family: var(--font-mono);
    font-size: 0.82rem;
    line-height: 1.55;
    white-space: pre;
    overflow-wrap: normal;   /* undo the inline-code rule inside pre */
}

/* ── Tables ── */
.msg-stream-text .md-table-wrap { overflow-x: auto; margin: 0 0 0.75em; }
.msg-stream-text table {
    border-collapse: collapse;
    font-size: 0.84rem;
    min-width: 100%;
}
.msg-stream-text th,
.msg-stream-text td {
    padding: 5px 10px;
    border: 1px solid var(--glass-border);
    text-align: left;
}
.msg-stream-text th {
    background: rgba(255, 255, 255, 0.045);
    font-weight: 600;
}
```

Add to `:root`:
```css
    --font-mono: ui-monospace, 'Cascadia Code', 'Consolas', 'SFMono-Regular', monospace;
```
No webfont (§2.1 rule 3) — `Cascadia Code` and `Consolas` both ship with Windows.

⚠ **TRAP (this one is subtle and will cost you an hour if you miss it):** `overflow-x: auto` on
`<pre>` only works if **every flex ancestor has `min-width: 0`**. The chain is
`.message` (flex) → `.msg-body` (flex column) → `.msg-content`. A flex item's default
`min-width: auto` means it refuses to shrink below its content's intrinsic width, so a 200-character
code line **widens the whole bubble and then the whole chat area**, and no `overflow` on the `<pre>`
can save it. Add `min-width: 0` to `.msg-body` and `.msg-content`. Test with a deliberately
unwrappable 300-char line. Same applies to `.md-table-wrap`.

⚠ **TRAP:** `.message.assistant .msg-content` has `transition` on it (`style.css:709`) and P2.6
narrowed the property list. Markdown adds child elements whose layout changes as blocks arrive — make
sure `transition` does not include `height` or every chunk animates the bubble's growth. It should
already be fine; confirm.

### Verify
1. **Round-trip a torture reply.** Ask: *"Show me a markdown demo: a heading, bold, italic, inline
   code, a Python code fence, a nested bullet list, a numbered list, a 3-column table, a blockquote,
   a link to example.com, and a horizontal rule."* Every element renders correctly **during** the
   stream and after.
2. **Nothing flashes.** Watch closely while streaming: no bold that turns off, no code block that
   opens and closes, no table that appears broken then fixes itself, no `[link` that becomes a link.
   Record a screen capture if unsure — this is the acceptance criterion for `repairForStream`.
3. **Windows paths survive.** Ask "what is my desktop path" and confirm `C:\Users\...` renders with
   every backslash, outside a code span.
4. **Identifiers survive.** Ask about `session_id` and `snake_case` — no italics.
5. **Copy button** copies the exact code, shows "Copied", does not appear on a still-streaming fence.
6. **Wide code does not widen the bubble.** Send a 300-char single-line code fence. The `<pre>`
   scrolls horizontally; the bubble and chat area do **not** grow. (The `min-width: 0` trap.)
7. **XSS is structurally impossible.** Ask Jarvis to reply with literally
   `<img src=x onerror=alert(1)>` and `<script>alert(1)</script>` and `[x](javascript:alert(1))`.
   All three render as **visible literal text**. No alert. Then check
   `document.querySelectorAll('.msg-stream-text script, .msg-stream-text img').length === 0`.
8. **Fallback works.** Temporarily `throw` at the top of `render()`. Replies must still appear as
   plain text with a console warning. Revert.
9. **Frame time.** Re-run the P0.4 streaming measurement. p95 frame time must stay **< 16.7ms** and
   long tasks must stay at **0** for a 600-word reply. If not, `splitBlocks` is being called on the
   full string per frame without the last-block shortcut — that is the O(n²) failure mode.
10. **History.** Reopen a past conversation: assistant messages render markdown; user messages do not.
11. **Notes.** Expand a note with markdown: renders. Preview: plain.
12. `node --check web/script.js`; `node --experimental-detect-module --check web/js/markdown.js`.

### Rollback
`git checkout HEAD~1 -- web/script.js web/style.css` and delete `web/js/markdown.js`.

---

## P7 — POLLING, TIMERS AND LISTENER HYGIENE

**Goal:** Nothing runs when nothing is happening. No unbounded growth over a long session.
**Why:** Five separate leaks, all confirmed:

1. **The activity poller never stops.** `window.setInterval(poll, 2000)` (`script.js:2061`) stores no
   handle, so it cannot be cleared. `if (document.hidden) return` skips the *fetch* but the timer
   keeps firing forever — 30 wakeups a minute for the life of the tab, on a machine also running an
   embedding model. And it keeps polling long after the turn is verified.
2. **`backgroundActivitySeen` grows unboundedly.** A `Set` (`script.js:30`) that accumulates a key per
   activity row for the life of the tab. Leave the app open for a day of heavy use and it holds tens
   of thousands of strings.
3. **The notification EventSource reconnect stores no handle** (`script.js:2889`):
   `setTimeout(connectNotificationStream, 5000)` inside `onerror`. A flapping server fires `onerror`
   repeatedly and each one schedules another reconnect — **overlapping timers, each opening another
   EventSource**. Fixed 5s delay, no backoff, no cap.
4. **`notifLastId` is dead** — written at 2882, never read. It looks like resume support that was
   never finished. Either use it or delete it; do not leave a half-built mechanism.
5. **`preloadStarterAudio()` fires 10 sequential `POST /tts` on every page load** (`script.js:311`).
   Server-cached, so cheap for the server, but it is 10 round trips and 10 base64 blobs held in
   memory before the user has typed a character. On a cold start it competes with the embedding model
   for the same CPU.

Also: the reminders and notes panels each set a 30-second auto-close timer on every open
(`_remindersPanelTimer`, `_notesPanelTimer`). Those *are* cleared correctly. Leave them — but note in
the report that a panel closing itself while the user is reading it is a **UX** problem, addressed in
P10.6.

### Files
- edit: `web/script.js`
- edit: `app/api/dashboard.py` *(one additive change, P7.3 — explicitly permitted by §2.1 rule 9)*

### Steps

**P7.1 — Make the activity poller stoppable, visibility-aware, and self-terminating.**

```js
/* Activity polling.  [M14 P7.1]
   Was: setInterval(poll, 2000) with no stored handle, firing forever — 30
   wakeups a minute for the life of the tab, including while the tab was
   hidden and long after every verdict had landed.

   Now: one stoppable timer, paused on tab hide, and stopped once the turn's
   verdicts have settled. Verification is async but bounded (CHECKER_SETTLE_
   PROFILES, config.py:331), so polling forever was never necessary. */
let activityPollTimer = null;
let activityPollIdleTicks = 0;

const ACTIVITY_POLL_MS = 2000;
// 15 ticks = 30s with no new rows. The slowest settle profile is well inside
// that, so this stops shortly after the last verdict rather than never.
const ACTIVITY_POLL_MAX_IDLE_TICKS = 15;

function stopBackgroundActivityPolling() {
    if (activityPollTimer) { clearInterval(activityPollTimer); activityPollTimer = null; }
    activityPollIdleTicks = 0;
}

function startBackgroundActivityPolling() {
    stopBackgroundActivityPolling();        // never stack two pollers
    activityPollIdleTicks = 0;
    backgroundActivitySeen.clear();         // P7.2 — new turn, new key space
    activityPollTimer = setInterval(pollActivityOnce, ACTIVITY_POLL_MS);
    pollActivityOnce();
}

async function pollActivityOnce() {
    if (document.hidden) return;            // don't count hidden ticks as idle
    const newRows = await fetchActivityRows();   // returns count of unseen rows
    if (newRows > 0) {
        activityPollIdleTicks = 0;
    } else if (!isStreaming && ++activityPollIdleTicks >= ACTIVITY_POLL_MAX_IDLE_TICKS) {
        stopBackgroundActivityPolling();
    }
}

// Pause the timer entirely while hidden, instead of firing and returning early.
document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
        if (activityPollTimer) { clearInterval(activityPollTimer); activityPollTimer = null; }
    } else if (isStreaming || activityPollIdleTicks < ACTIVITY_POLL_MAX_IDLE_TICKS) {
        if (!activityPollTimer) activityPollTimer = setInterval(pollActivityOnce, ACTIVITY_POLL_MS);
    }
});
```

⚠ **TRAP:** never let `activityPollIdleTicks` increment while `isStreaming` — a long agent turn is
legitimately silent between tool calls, and stopping the poller mid-turn means FAIL verdicts never
produce a correction bubble (the M5 feature). That is a **regression in truthfulness**, not a cosmetic
one. The `!isStreaming` guard above is load-bearing.

⚠ **TRAP:** `startBackgroundActivityPolling` is currently called **once** at startup. It must now be
called **per turn**, from `sendMessage` right after `resetTurnPanels()`. Find the existing call site
and move it. If you leave it at startup only, the poller stops after 30s and never restarts.

⚠ **TRAP:** the existing function body does both the fetch and the render. Split it: `fetchActivityRows()`
returns the count of newly-seen rows so `pollActivityOnce` can decide about idling. Keep the
`try/catch` that swallows everything — `CLAUDE.md` rule #6.

**P7.2 — Bound `backgroundActivitySeen`.**
`clear()` per turn (above) is the main fix, since keys are turn-scoped anyway. Add a belt-and-braces
cap for a single very long turn:
```js
// A single turn can emit a lot of rows (16 agent steps × tool + verdict + cache).
// 2000 is far above any real turn and far below "leak".
if (backgroundActivitySeen.size > 2000) backgroundActivitySeen.clear();
```
⚠ **TRAP:** clearing mid-turn re-shows already-seen rows. `appendActivity` is append-only, so you get
duplicates in the panel. Acceptable at 2000 rows (it will never happen in practice) but note it.

**P7.3 — Fix the EventSource reconnect: single handle + exponential backoff.**

```js
/* Reminder notification stream.  [M14 P7.3]
   Was: onerror did `setTimeout(connectNotificationStream, 5000)` with no
   stored handle. EventSource.onerror fires repeatedly on a flapping server,
   so each error scheduled ANOTHER reconnect — overlapping timers, each
   opening another EventSource. A server restart could leave several live
   streams pushing duplicate reminder toasts. */
let notifEventSource = null;
let notifReconnectTimer = null;
let notifRetryDelay = 1000;
const NOTIF_RETRY_MAX = 30000;

function connectNotificationStream() {
    if (notifReconnectTimer) { clearTimeout(notifReconnectTimer); notifReconnectTimer = null; }
    if (notifEventSource) { try { notifEventSource.close(); } catch (_) {} notifEventSource = null; }

    const es = new EventSource(`${API}/api/notifications/stream`);
    notifEventSource = es;

    es.onopen = () => { notifRetryDelay = 1000; };   // reset backoff on success

    es.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.type === 'reminder') {
                showReminderToast(data);
                playNotificationSound();
                if (isPanelOpen(remindersPanel)) openRemindersPanel();
            }
        } catch (_) {}
    };

    es.onerror = () => {
        // Ignore errors from a stream we already replaced.
        if (notifEventSource !== es) return;
        try { es.close(); } catch (_) {}
        notifEventSource = null;
        if (notifReconnectTimer) return;             // one pending reconnect, ever
        notifReconnectTimer = setTimeout(() => {
            notifReconnectTimer = null;
            connectNotificationStream();
        }, notifRetryDelay);
        notifRetryDelay = Math.min(notifRetryDelay * 2, NOTIF_RETRY_MAX);
    };
}

// Don't hold an idle stream open while the tab is hidden; reconnect on return.
document.addEventListener('visibilitychange', () => {
    if (document.hidden) return;                      // keep it open — reminders
                                                      // must fire while hidden.
    if (!notifEventSource && !notifReconnectTimer) connectNotificationStream();
});
```

⚠ **TRAP:** do **not** close the notification stream when the tab hides. Its whole purpose is firing
reminders while the user is doing something else. The `visibilitychange` handler above only *recovers*
a dead stream on return — it must never close a live one. This is the opposite of the activity poller,
and getting it backwards silently breaks reminders.

**P7.4 — Decide on `notifLastId`.**
Delete it. Using it would require a `Last-Event-ID` / `?since=` parameter on
`GET /api/notifications/stream`, which is a backend change and a real design question (what happens
to a reminder that fired while no browser was open?). Half-built state is worse than none.

Add a one-line comment where it was:
```js
// Removed notifLastId (M14 P7.4): written, never read. Resuming a missed
// notification needs a `since` param on the SSE endpoint and a decision about
// reminders that fire with no browser open — out of scope for a UI milestone.
```

**P7.5 — Make starter-audio preload lazy and parallel-capped.**

```js
/* Starter audio.  [M14 P7.5]
   Was: 10 sequential POST /tts on every page load, before the user typed
   anything. Server-cached so cheap for the server, but 10 round trips and 10
   base64 blobs resident in memory, competing with startup for CPU on a machine
   that is also loading an embedding model.

   Now: preload ONE (so the first realtime query still has instant audio) and
   fetch the rest on first idle, only if TTS is actually enabled. */
async function preloadStarterAudio() {
    if (!(ttsPlayer && ttsPlayer.enabled)) return;   // TTS off → don't fetch at all
    await fetchStarter(0);
    const rest = () => { for (let i = 1; i < STARTERS.length; i++) fetchStarter(i); };
    if ('requestIdleCallback' in window) requestIdleCallback(rest, { timeout: 8000 });
    else setTimeout(rest, 4000);
}
```
⚠ **TRAP:** the TTS toggle can be flipped *after* load. Call `preloadStarterAudio()` from the toggle's
change handler too, and make it idempotent (a `_starterPreloadStarted` flag) so flipping it twice does
not double-fetch.

**P7.6 — One `visibilitychange` listener, not three.**
P2.3 added one for the orb, P7.1 and P7.3 add more. Consolidate into a single handler that calls named
functions, so the ordering is explicit and readable:
```js
// Single visibility handler. Three separate listeners existed after P2/P7 and
// their relative order was undefined, which matters because the orb resume and
// the poller restart both read `isStreaming`.
document.addEventListener('visibilitychange', () => {
    const hidden = document.hidden;
    orbVisibilityChanged(hidden);
    activityPollVisibilityChanged(hidden);
    notificationVisibilityChanged(hidden);
});
```

### Verify
1. **Idle, tab focused, no chat:** DevTools Performance record for 10s. Expect **zero** `setInterval`
   firings after the poller self-stops (~30s after the last turn). Combined with P2, "idle CPU tab
   hidden" in `docs/UI_BASELINE.md` should now be **≈ 0%** with no residue.
2. **Poller restarts per turn.** Send a message; confirm activity rows appear. Wait 60s; confirm the
   poller has stopped (`activityPollTimer === null` in the console). Send another; confirm rows appear
   again. **This is the regression this phase is most likely to introduce.**
3. **FAIL verdicts still produce correction bubbles.** Trigger a failing action (e.g. ask it to open
   an application that does not exist). The correction bubble must still appear. If it does not, the
   poller stopped too early — re-read P7.1's first trap.
4. **Long agent turn.** Run a genuine multi-step task lasting > 30s. The poller must **not** stop
   mid-turn.
5. **EventSource backoff.** Stop the server. In the Network tab, confirm reconnect attempts at
   ~1s, 2s, 4s, 8s, 16s, 30s, 30s… and **exactly one** in flight at a time. Restart the server:
   it reconnects and a fired reminder still toasts. Before the fix you would see a growing pile.
6. **No duplicate toasts.** Restart the server 3 times in a row, then fire a reminder. Exactly **one**
   toast. (This is the overlapping-EventSource bug.)
7. **Reminders fire while the tab is hidden.** Set a reminder 1 minute out, switch to another
   application, wait. The native notification appears. If it does not, you closed the stream on hide —
   read P7.3's trap.
8. **Starter audio.** With TTS off, hard reload → Network shows **zero** `POST /tts`. With TTS on →
   exactly one immediately, the rest after idle. A realtime query still gets its starter audio.
9. `backgroundActivitySeen.size` after 10 turns is small (turn-scoped), not cumulative.
10. `node --check web/script.js`; 215 tests pass.

### Rollback
`git checkout HEAD~1 -- web/script.js`

---

## P8 — DESIGN TOKENS, Z-INDEX SCALE, CSS FILE SPLIT

**Goal:** One place to change a colour. One place to reason about stacking. Files small enough to
navigate.
**Why:** After P1 the stylesheet is ~3570 lines and internally consistent, but two structural problems
remain.

**The z-index situation is actively broken.** Every value, verified:

| Value | Selector | Line |
|---|---|---|
| 0 | `#orb-container` | 114 |
| 1 | `.mode-btn` | 306 |
| 3 | `.history-item-menu` | 4302 |
| 5 | `.chat-area`, `.history-item.menu-open` | 448, 4296 |
| 6 | `.activity-panel`, `.search-results-widget` | 1298, 1467 |
| 8 | `.scroll-fab` | 462 |
| 10 | `.header`, `.input-bar` | 198, 1085 |
| 19 | `.panel-overlay` | 1243 |
| 21 | `.settings-panel` | 1666 |
| 22 | `.history-overlay` | 4102 |
| 23 | `.history-panel` | 4114 |
| 25 | `.orb-dashboard` | 1777 |
| **40** | **`.history-dialog-backdrop`** | **4373** |
| **100** | `.cam-panel`, `.toast-container` | 910, 1262 |
| **1100** | `.jarvis-panel` (reminders/notes) | 3377 |
| **9999** | `.reminder-toast-container` | 3925 |

The modal dialog sits at **40**. The notes panel sits at **1100** and a reminder toast at **9999**.
So a reminder firing while the "Delete conversation?" dialog is open **paints over the dialog**, and
the notes panel does too. A modal that is not on top is not modal — and the dialog it covers is the one
that permanently deletes a conversation with no trash folder (`CLAUDE.md` §19). That is the worst
possible dialog to obscure.

The numbers are also unmaintainable: `9999` is a "win at all costs" value, and the gap from 25 to 100
to 1100 means the next developer picks another arbitrary number.

### Files
- edit: `web/style.css` → becomes a 7-file `@import` set under `web/css/`
- edit: `web/index.html` (one `<link>` → still one `<link>`)

### Steps

**P8.1 — Define a z-index scale in `:root`, and use it everywhere.**

```css
    /* ── Stacking scale ─────────────────────────────── [M14 P8.1] ──
       Every z-index in the app comes from here. Adding a raw number is a
       bug: it is how a modal dialog ended up at 40 while a toast sat at
       9999, so a reminder firing during "Delete conversation?" painted
       over the confirmation for a permanent, unrecoverable delete.

       Gaps of 100 leave room to insert a layer without renumbering.     */
    --z-orb:            0;    /* ambient background, never interactive */
    --z-content:      100;    /* chat area */
    --z-chrome:       200;    /* header, input bar — above content       */
    --z-fab:          300;    /* scroll-to-bottom, floats over content   */
    --z-panel:        400;    /* side panels: activity, search, settings,
                                 orb dashboard, history                  */
    --z-panel-overlay:390;    /* sits just UNDER panels, over content    */
    --z-float:        500;    /* draggable panels: cam, reminders, notes */
    --z-modal-back:   700;    /* dialog backdrop                         */
    --z-modal:        710;    /* the dialog itself                       */
    --z-toast:        800;    /* transient, must be seen — but see below  */
    --z-dev:        99999;    /* dev perf overlay only (P0.4)            */
```

**The one real decision here:** should a reminder toast be above or below a modal dialog?
**Below.** A reminder is informational and re-appears in the reminders panel; a modal is a blocking
question the user must answer, and one of them deletes data irreversibly. So `--z-toast: 800` is above
panels and floats but the **modal is the exception**: give `.history-dialog-backdrop` /
`.history-dialog` a higher pair.

Cleanest fix: put toasts at `--z-toast: 600` (above panels/floats, below modals) and leave modal at
700/710. Do that. Write the reasoning as a comment — a future developer *will* bump the toast again
otherwise.

Mapping to apply:
| Selector | Old | New |
|---|---|---|
| `#orb-container` | 0 | `var(--z-orb)` |
| `.mode-btn` | 1 | leave (local stacking inside the switch) |
| `.history-item-menu` | 3 | leave |
| `.history-item.menu-open` | 5 | leave — **keep the load-bearing comment** |
| `.chat-area` | 5 | `var(--z-content)` |
| `.activity-panel`, `.search-results-widget` | 6 | `var(--z-panel)` |
| `.scroll-fab` | 8 | `var(--z-fab)` |
| `.header`, `.input-bar` | 10 | `var(--z-chrome)` |
| `.panel-overlay` | 19 | `var(--z-panel-overlay)` |
| `.settings-panel`, `.orb-dashboard`, `.history-panel` | 21/25/23 | `var(--z-panel)` |
| `.history-overlay` | 22 | `var(--z-panel-overlay)` |
| `.cam-panel`, `.jarvis-panel` | 100/1100 | `var(--z-float)` |
| `.toast-container`, `.reminder-toast-container` | 100/9999 | `var(--z-toast)` |
| `.history-dialog-backdrop` | 40 | `var(--z-modal-back)` |
| `.history-dialog` | 21 | `var(--z-modal)` |

⚠ **TRAP:** `.activity-panel` (6) and `.settings-panel` (21) currently differ, and settings deliberately
covers activity. Flattening both to `var(--z-panel)` makes **source order** decide. Check every pair
that can be open simultaneously: activity + settings, activity + history, search + settings, orb
dashboard + anything. If any pair regresses, give the one that must win `calc(var(--z-panel) + 10)`
with a comment saying which pair forced it. **Do not** guess — open them in pairs and look.

⚠ **TRAP:** `.history-panel` at 23 sits **above** `.history-overlay` at 22, and the overlay is
mobile-only and deliberately non-blocking (`CLAUDE.md` §19). Flattening the panel to `--z-panel` (400)
and the overlay to `--z-panel-overlay` (390) preserves the relationship. Verify on mobile at 480px.

⚠ **TRAP:** `.cam-panel` and `.jarvis-panel` are both draggable and both now at `--z-float`. They can
overlap. Source order decides, which means whichever is defined later always wins regardless of which
the user touched last. **Add click-to-front:** on `mousedown`, set `style.zIndex = 'calc(var(--z-float) + 1)'`
on the touched panel and clear it on the others. Small, and it is the behaviour a user expects from
floating panels.

**P8.2 — Remove the five `!important` on `.send-btn`** (`1168–1177`). They exist to beat `.action-btn`
(1142), which has **identical specificity** and loses on source order anyway. Delete them, confirm the
button looks identical, and add:
```css
/* No !important here. .send-btn beats .action-btn on source order alone —
   both are single-class selectors. The 5 !importants that used to be here
   were cargo-culted and made .stop-btn (P3.5) unstylable. */
```

**P8.3 — Audit remaining `!important`.** `grep -c '!important' web/style.css`. For each, either delete
it (if source order suffices) or leave it with a comment saying what it beats. Two that **must stay**:
- `.history-panel [hidden] { display: none !important }` — documented in `CLAUDE.md` §13 as
  load-bearing; several rules set `display` and would otherwise beat the UA `[hidden]` rule, leaving
  dialogs permanently visible. **Keep the existing comment verbatim.**
- Any `prefers-reduced-motion` override added in P2.1 — it must beat everything by design.

**P8.4 — Split into `web/css/`.** Order matters; the cascade is the API.

| File | Contents | Approx |
|---|---|---|
| `tokens.css` | `:root` — colours, radii, transitions, fonts, z-scale, `color-scheme` | 90 |
| `base.css` | reset, `body`, star field, scrollbars, `.sr-only`, `.glass-panel`, focus-visible, the global `prefers-reduced-motion` block | 260 |
| `layout.css` | `.app`, `.header`, `.chat-area`, `.input-bar`, `#orb-container`, `.welcome-screen`, `.scroll-fab` | 700 |
| `chat.css` | `.message`, `.msg-*`, stream cursor, correction bubbles, **markdown (P6)**, **stream errors (P3)** | 700 |
| `panels.css` | activity, search results, settings, orb dashboard, history drawer + dialogs, `.panel-overlay` | 1100 |
| `floats.css` | `.cam-panel`, `.jarvis-panel` (reminders/notes/todos), toasts, reminder toasts | 700 |
| `responsive.css` | **both** media queries, consolidated | 200 |

`web/style.css` becomes only:
```css
/* ═══════════════════════════════════════════════════════════════════
   J.A.R.V.I.S — stylesheet entry point.  M14 P8.4
   ───────────────────────────────────────────────────────────────────
   ORDER IS THE API. The cascade resolves same-specificity conflicts by
   source order, so moving a line here changes rendering. In particular:
     • tokens.css must be first — everything reads its custom properties
     • responsive.css must be last — media queries override base rules at
       equal specificity, so they only work from the bottom
   ═══════════════════════════════════════════════════════════════════ */
@import url('css/tokens.css');
@import url('css/base.css');
@import url('css/layout.css');
@import url('css/chat.css');
@import url('css/panels.css');
@import url('css/floats.css');
@import url('css/responsive.css');
```

⚠ **TRAP — read this before choosing `@import`:** `@import` inside a stylesheet is **serial**. The
browser must fetch `style.css`, parse it, *then* discover and fetch seven more files. That is a
request chain, and CSS is render-blocking — you would trade maintainability for a slower first paint,
which contradicts §2.3.

**Therefore: use seven `<link>` tags in `index.html` instead**, which the browser fetches in parallel:
```html
    <!-- Seven files, not @import: @import is serial (parse style.css, THEN
         discover and fetch each child), which chains render-blocking requests.
         <link> lets the browser fetch all seven in parallel.
         ORDER IS THE API — see the comment in css/tokens.css.  [M14 P8.4] -->
    <link rel="stylesheet" href="css/tokens.css?v=20260801">
    <link rel="stylesheet" href="css/base.css?v=20260801">
    <link rel="stylesheet" href="css/layout.css?v=20260801">
    <link rel="stylesheet" href="css/chat.css?v=20260801">
    <link rel="stylesheet" href="css/panels.css?v=20260801">
    <link rel="stylesheet" href="css/floats.css?v=20260801">
    <link rel="stylesheet" href="css/responsive.css?v=20260801">
```
Keep `web/style.css` as a **7-line file containing only the `@import` set**, purely so any bookmarked
or cached reference to it still works — but do not link it from `index.html`. Note this in the report.
`api-monitor.html` currently reuses `style.css`; leave it pointing there until P11.

⚠ **TRAP:** splitting must be a **pure move**. Do not reformat, reorder within a section, or "tidy"
while cutting. Verify byte-for-byte:
```powershell
# Concatenated split files must equal the pre-split file, ignoring the banner
# comments you add. Diff the rule inventory, not the raw bytes.
Select-String -Path web\css\*.css -Pattern '\{\s*$' | ForEach-Object { $_.Line.Trim() } | Sort-Object > $env:TEMP\after.txt
git show HEAD:web/style.css | Select-String -Pattern '\{\s*$' | ForEach-Object { $_.Line.Trim() } | Sort-Object > $env:TEMP\before.txt
Compare-Object (Get-Content $env:TEMP\before.txt) (Get-Content $env:TEMP\after.txt)
```
Empty output = clean move.

**P8.5 — Consolidate the two media queries** into `responsive.css`. After P1 there is one `768px` and
one `480px` block. Keep them in that order (`768` then `480`) — `480` must override `768`.

⚠ **TRAP:** `.speech-widget`, `.speech-widget-text`, `.speech-widget-label`, `.new-chat-btn`,
`.mode-btn-text` are styled **only** inside media queries with no base rule. `.new-chat-btn` and
`.mode-btn-text` **do** exist in `index.html` (verified — the new-chat header button and the mode pill
label), so their base styling comes from `.btn-icon` / `.mode-btn`; that is fine, leave them.
`.speech-widget*` does **not** appear in `index.html` — grep `script.js` before deleting; it may be
created dynamically. If it is genuinely dead, delete and say so in the report. **Do not delete on the
basis of the HTML alone.**

**P8.6 — Add the tokens that are used but never declared.** `--orb-idle-opacity` is consumed at
`style.css:145` but only ever set by JS (`orb.js applyGlobals`), relying on the `0.35` fallback.
Declare it in `tokens.css` with a comment so a reader is not hunting for it:
```css
    /* Set at runtime by orb.js applyGlobals() from localStorage['orbGlobals'].
       Declared here only so the default is discoverable — the fallback in
       layout.css carried it before and nothing told you where it came from. */
    --orb-idle-opacity: 0.35;
```
Also add `--font-mono` (P6) and `--input-bar-h` / `--panel-gap` if they are used-but-undeclared
(grep them).

### Verify
- **Every screenshot from P0.2 re-taken and compared.** This phase should produce **zero** visual
  change. Any difference is a mistake, not an improvement.
- **Stacking matrix — open each pair and confirm the right one is on top:**
  | Pair | Expected on top |
  |---|---|
  | Delete dialog + reminder toast fires | **dialog** (this is the bug being fixed) |
  | Delete dialog + notes panel open | **dialog** |
  | Rename dialog + reminder toast | **dialog** |
  | Settings + activity | settings |
  | History + activity | whichever the user opened last, and neither blocks the composer |
  | Orb dashboard + settings | last opened |
  | Cam panel + notes panel | whichever was clicked last (**new** click-to-front) |
  | Any panel + header/input bar | panel |
  | Toast + any side panel | toast |
- `grep -nE 'z-index:\s*[0-9]' web/css/*.css` returns only the four documented local-stacking cases
  (`.mode-btn`, `.history-item-menu`, `.history-item.menu-open`, `--z-*` declarations in `tokens.css`).
- Network tab: seven CSS files, fetched **in parallel** (waterfall bars overlap, not stair-step).
  Combined transfer size ≤ the single-file size from P1.
- First paint not regressed vs the P1 measurement. Record it.
- Mobile 768 and 480 both still correct.
- `grep -c '!important' web/css/*.css` — every remaining one has a comment.

### Rollback
`git checkout HEAD~1 -- web/ ` (this phase moves files; a partial revert is worse than a full one).

---

## P9 — `script.js` → ES MODULES

**Goal:** Turn a 3583-line file into ~12 focused modules with explicit dependencies.
**Why:** Maintainability only. **This phase has the highest risk-to-visible-reward ratio in the plan.**
It is placed after P1–P8 deliberately: every earlier phase delivers something the user can see, and
none of them need modules. If time runs out, **stop before P9** — a well-organised broken app is worth
less than a monolithic working one.

⚠ **Do not start P9 unless P1–P8 are committed, verified, and you have time to finish it in one
sitting.** A half-modularised `script.js` is the worst possible state to leave the repo in.

### The two hard blockers (both verified)

1. **`script.js:665` reassigns `ORB_STATES`**, a mutable `let` declared at `orb.js:31`. Both are classic
   scripts sharing one global scope, so this works today. **Under ES modules it throws** — module
   bindings are read-only from the importing side.
2. **`initOrb` (`script.js:715`) monkey-patches `orb.setState` and `orb.setStateInstant`** to also poke
   the status badge. Wrapping an imported object's methods is legal but it is invisible coupling, and it
   means the badge silently stops updating if anyone constructs the orb differently.

Both need a **real API** on the orb, not a workaround. This is genuinely a small improvement to the
design, not just module plumbing.

### Files
- new: `web/js/*.js` (~12 modules)
- edit: `web/orb.js` → `web/js/orb.js`
- edit: `web/index.html`
- delete: `web/script.js` (at the very end, once nothing references it)

### Module layout

```
web/js/
├── main.js          entry point: imports everything, wires init order, one try/catch per subsystem
├── config.js        API base, CHAT_BASE_PATH, CAM_BYPASS_TOKEN, all timing constants
├── state.js         isStreaming, sessionId, currentMode, settings — the shared mutable state
├── dom.js           el(), icon(), replaceChildren()            (exists, P4)
├── icons.js         every SVG path as a named constant          (new, P10.4)
├── markdown.js      streaming markdown renderer                 (exists, P6)
├── api.js           every fetch() in one place: chat stream, reminders, notes, todos,
│                    transcribe, tts, health, activity, history
├── orb.js           moved from web/orb.js, plus the new onStateChange API
├── chat.js          sendMessage, addMessage, addCorrection, typing indicator, scroll
├── stream.js        the SSE read loop, frame dispatch, error/stop handling (P3)
├── voice.js         PTT, Web Speech, Whisper fallback, TTSPlayer, starter audio
├── vision.js        camera, capture, cam panel drag
├── panels.js        activity, search results, settings, orb dashboard, overlay,
│                    drag/minimise/click-to-front shared logic
├── history.js       the whole M11 history drawer + M12 URL routing
├── reminders.js     reminders panel + toasts + notification EventSource
├── notes.js         notes + todos panels
├── commands.js      the command registry (P10.2) — every module registers into it
├── shortcuts.js     keyboard map, rendered from commands.js (P10.3)
└── devtools.js      perf overlay                                (exists, P0)
```

### Steps

**P9.1 — Fix blocker 1: give the orb a real state-config API.**

In `orb.js`, stop exporting a mutable binding. Export functions:
```js
// ORB_STATES was a mutable `let` that script.js reassigned wholesale
// (old script.js:665). That worked only because both files were classic
// scripts sharing one global scope; under ES modules an imported binding is
// read-only. Reassignment was always the wrong shape anyway — the orb should
// own its state table and expose intent, not a variable.  [M14 P9.1]
const STATES = { idle: {...}, listening: {...}, /* … */ };

export function getStateConfig(name) { return { ...STATES[name] }; }        // copy
export function setStateConfig(name, patch) { Object.assign(STATES[name], patch); }
export function replaceStateConfigs(all) {
    for (const k of Object.keys(STATES)) if (all[k]) Object.assign(STATES[k], all[k]);
}
export function stateNames() { return Object.keys(STATES); }
```
Then `script.js:665`'s wholesale reassignment becomes `replaceStateConfigs(saved)`.

⚠ **TRAP:** `getStateConfig` must return a **copy**. The orb dashboard reads a config, mutates it as
the user drags a slider, and writes it back. If it gets the live object, the "Reset Defaults" button
has nothing to reset to. Verify by dragging every slider then hitting Reset.

**P9.2 — Fix blocker 2: replace the monkey-patch with an observer.**

In `orb.js`:
```js
const stateListeners = new Set();

/** Notified on every state change, including instant ones. Replaces
 *  script.js's monkey-patching of setState/setStateInstant to poke the status
 *  badge — invisible coupling that broke silently if the orb was built
 *  differently.  [M14 P9.2] */
export function onStateChange(fn) {
    stateListeners.add(fn);
    return () => stateListeners.delete(fn);   // unsubscribe
}

function emitState(name) {
    for (const fn of stateListeners) {
        try { fn(name); } catch (e) { console.warn('[orb] state listener threw', e); }
    }
}
```
Call `emitState(name)` at the end of both `setState` and `setStateInstant`. Then in `main.js`:
```js
orb.onStateChange(updateStatusBadge);
```
Delete the monkey-patch block from `initOrb`.

⚠ **TRAP:** listeners must be wrapped in `try/catch` (as above). A throwing badge updater must not stop
the orb from changing state — `CLAUDE.md` rule #6.

**P9.3 — Move in dependency order, one module per commit.** This is the discipline that makes P9
survivable. Suggested order — leaves are first, so nothing ever imports something that has not moved:

`config.js` → `state.js` → `icons.js` → `api.js` → `orb.js` → `voice.js` → `vision.js` →
`panels.js` → `reminders.js` → `notes.js` → `history.js` → `stream.js` → `chat.js` → `main.js`

After each move: `node --experimental-detect-module --check web/js/<file>.js`, load the page, confirm
**zero** console errors, and confirm the feature that module owns still works. Commit. **Do not batch.**

**P9.4 — Handle circular imports.** `chat.js` needs `panels.js` (auto-open activity), and `panels.js`
needs `chat.js` (correction bubbles). This mirrors the backend's own solution:
`app/services/agent/deps.py` exists precisely to break circular imports (`CLAUDE.md` §3). Do the same
— a tiny `bus.js`:
```js
/* Minimal pub/sub to break import cycles. Same reason app/services/agent/deps.py
   exists on the backend: two modules legitimately need each other, and an
   event boundary is cheaper than a wrong dependency direction.  [M14 P9.4]

   Keep this SMALL. It is a cycle-breaker, not an architecture. If you find
   yourself routing normal calls through here, you have made the code harder
   to read, not easier. */
const handlers = new Map();
export function on(evt, fn) {
    if (!handlers.has(evt)) handlers.set(evt, new Set());
    handlers.get(evt).add(fn);
    return () => handlers.get(evt).delete(fn);
}
export function emit(evt, payload) {
    for (const fn of handlers.get(evt) || []) {
        try { fn(payload); } catch (e) { console.warn(`[bus] ${evt} handler threw`, e); }
    }
}
```
Events: `turn:start`, `turn:end`, `activity:row`, `verdict:fail`, `session:change`, `orb:state`.
**Six events, not sixty.** Everything else is a direct import.

**P9.5 — `index.html`.** Replace both classic `<script>` tags with one module:
```html
    <script type="module" src="js/main.js?v=20260801"></script>
```
⚠ **TRAP:** modules are **deferred** by default, so they run after DOM parse — which means the
`document.getElementById` calls that currently sit at top level in `script.js` will now find their
elements (they already do, since the tag is at the end of `<body>`). But module top-level code also
runs **once, in import order**, so any module doing `document.getElementById` at import time depends on
`main.js` being loaded last. **Move all DOM lookups into an exported `init()`** per module, called from
`main.js`. Do not rely on import order for DOM readiness.

⚠ **TRAP:** `StaticFiles` must serve `.js` as `text/javascript` for modules to load. FastAPI's
`StaticFiles` uses `mimetypes`, which is correct on Windows in normal setups — but a mangled registry
key (`HKCR\.js\Content Type` set to `text/plain`, which some old installers do) will make **every
module fail** with a MIME type error while classic scripts keep working. If you see
`Failed to load module script: … MIME type of "text/plain"`, that is the cause. Confirm early with the
P0.4 module tag, which already exercises this path — **that is one reason P0 loads `devtools.js` as a
module.**

**P9.6 — Delete `web/script.js`** only after `grep -rn 'script\.js' web/ app/` returns nothing but
comments and this plan.

### Verify
- **Every single feature.** This phase can silently break anything. Work the list: send a text message ·
  markdown renders · stop button · retry after failure · PTT voice · Web Speech fast path · Whisper
  fallback · TTS on/off · camera open/close/capture/vision-mode · cam panel drag + minimise · activity
  panel · correction bubble on FAIL · search results widget · settings toggles (all three) · orb
  dashboard every slider + every state tab + reset · status badge tracks orb state · history drawer
  open/search/load-more/select/rename/delete · deep link hard-refresh at `/jarvis/c/<id>` ·
  Back/Forward · new chat · reminders panel + create/done/snooze/delete · reminder toast + its three
  buttons · native notification · notes CRUD · todo CRUD + add-item Enter · scroll FAB · welcome chips ·
  startup brief · toasts · char counter · textarea auto-resize.
- **Zero console errors and zero warnings** on load and through the above.
- **Both mounts:** `/jarvis/` and `/app/` both load. This is where a wrong absolute path shows up.
- **Deep link:** hard-refresh at `/jarvis/c/<real-id>` loads styled, with the conversation. Proves
  `<base href>` still covers the module paths.
- `docs/UI_BASELINE.md`: total JS transfer size within +10% of baseline (§2.3). More files means more
  requests — if it regresses noticeably, say so; do **not** add a bundler (§2.1 rule 2).
- 215 tests pass. `scripts/_history_ui_check.py` (the Playwright history check) still passes — it is the
  only automated frontend test in the repo, so run it.

### Rollback
`git reset --hard <last pre-P9 commit>`. Per-module commits mean you can also stop midway and revert
just the last one.

---

## P10 — UX FEATURES

**Goal:** The interaction improvements, on the now-clean foundation.
**Why:** These are the things a user would actually name if asked what is missing. The feature list is
**closed** (§2.1 rule 5) — implement these, nothing else.

### P10.1 — Header density

**Problem:** 8 icon buttons plus the logo, tagline, mode pill and status badge in a 64px bar. At the
480px breakpoint they shrink further, and 32×32 targets crowded edge-to-edge are hard to hit even
though they technically pass WCAG 2.2 SC 2.5.8 (24×24 minimum). The real issue is **density**, not
target size.

**Fix — group by frequency, do not just shrink:**

| Keep visible | Move into an overflow menu |
|---|---|
| History (opens the drawer — highest use) | API Monitor |
| New chat | Orb Customization |
| Activity | Settings |
| Search results (already auto-hides) | |

- Add one `⋯` button (`#header-more`) that opens a small menu with the three moved items.
- Keep `title` **and** `aria-label` on everything (already done — do not regress it).
- Menu: `role="menu"`, items `role="menuitem"`, arrow-key navigation, Escape closes, focus returns to
  the `⋯` button. Match the existing `.history-item-menu` pattern so there is one menu idiom, not two.
- At ≤ 480px, also hide `.tagline` (probably already hidden — check) and collapse the status badge to
  its dot with the text in `aria-label`.

⚠ **TRAP:** the `⋯` menu is a popover inside a `position: relative` header with
`backdrop-filter`. A `backdrop-filter` on an ancestor creates a **containing block for fixed-position
descendants** — a `position: fixed` menu will be positioned relative to the header, not the viewport.
This is the same class of bug as the documented `.history-item.menu-open` z-index issue. Use
`position: absolute` inside the header and let it overflow, or move the menu outside `.header` in the
DOM. Test at 480px where it is most likely to clip.

### P10.2 — Command palette (`Ctrl+K`)

The single feature that most fits a JARVIS identity, and it makes P10.1's overflow menu low-cost
because everything stays reachable.

**Registry-driven (§2.1 rule 8 — no hand-maintained list).** `commands.js`:
```js
/* Command registry.  [M14 P10.2]
   Every module registers its own commands at init. There is deliberately no
   central array of commands: a hand-maintained list is the frontend version of
   the hardcoded command lists CLAUDE.md rule #10 bans, and it would drift the
   moment someone adds a panel. The palette and the shortcut overlay (P10.3)
   both render from THIS, so they can never disagree. */
const commands = [];

export function registerCommand({ id, title, group, keywords = '', shortcut = null, run, when = null }) {
    if (commands.some(c => c.id === id)) {
        console.warn(`[commands] duplicate id ${id}`);   // fail loud in dev, soft in prod
        return;
    }
    commands.push({ id, title, group, keywords, shortcut, run, when });
}

/** `when` lets a command hide itself when it makes no sense — e.g. "Stop
 *  generating" only exists while streaming. */
export function availableCommands() {
    return commands.filter(c => !c.when || c.when());
}
```

Commands to register (this list **is** the closed feature set):

| Group | Command | Shortcut |
|---|---|---|
| Chat | New conversation · Stop generating (`when: isStreaming`) · Regenerate last reply · Copy last reply · Scroll to latest | `Ctrl+Shift+O` · `Esc` · — · — · — |
| Panels | Toggle history · Toggle activity · Toggle search results · Toggle settings · Toggle orb customization · Toggle reminders · Toggle notes | `Ctrl+Shift+H` · `Ctrl+Shift+A` · — · `Ctrl+,` · — · — · — |
| Voice | Toggle TTS · Push to talk (hold) | `Ctrl+Shift+M` · `Ctrl+Shift` |
| Vision | Toggle camera · Capture and ask | — |
| View | Toggle reduced motion (session override) · Toggle perf overlay | — |
| Navigate | Recent conversations (last 10, from `historyState`) | — |
| Links | Control Center · Watcher · API Monitor · Health | — |

**Search:** simple subsequence fuzzy match over `title + group + keywords`, scoring (a) consecutive-run
length, (b) word-boundary starts, (c) shorter titles first. ~40 lines. **No fuzzy library.**

**Accessibility — implement exactly this, it is the standard combobox pattern:**
- Trigger: `Ctrl+K` (and `Cmd+K`). Must **not** fire while the textarea is focused **unless** the
  textarea is empty — otherwise you steal a text-editing shortcut.
- Dialog: `role="dialog" aria-modal="true"`, focus moves to the input, focus is **trapped**, Escape
  closes and returns focus to where it was.
- Input: `role="combobox" aria-expanded="true" aria-controls="palette-list" aria-activedescendant="<id>"`.
- List: `role="listbox"`; items `role="option"` with `aria-selected`.
- Arrow Up/Down move `aria-activedescendant` (do **not** move DOM focus off the input — that breaks
  typing). Enter runs. Home/End jump.
- A `role="status"` live region announcing "N commands".
- Empty state: "No matching commands."

⚠ **TRAP:** `Ctrl+K` is browser "focus address bar" on some setups and a readline binding in others.
`preventDefault()` is required. Also check it does not collide with the existing Ctrl+Shift PTT
handler — read the PTT keydown/keyup logic before adding a global handler, because PTT tracks modifier
state and a `preventDefault` in the wrong place can leave it stuck "held".

⚠ **TRAP:** the palette must be reachable **while streaming** (that is when you need "Stop"), so do not
gate it on `isStreaming`.

### P10.3 — Keyboard shortcut overlay (`?`)

Rendered **from `commands.js`**, filtered to commands with a `shortcut`, grouped by `group`. Never a
second hand-written list — that is the whole point of the registry.

- Trigger: `?` (Shift+/) when **not** in a text field, and `F1`.
- Same dialog semantics as the palette (`role="dialog"`, focus trap, Escape).
- Include the shortcuts that predate the registry and must be documented anyway: **Ctrl+Shift** hold
  to talk (currently discovered by accident — `CLAUDE.md` §13 documents it but the UI does not),
  Enter to send, Shift+Enter for newline, Escape to stop.
- Add a `?` hint in the settings panel footer pointing at it, so it is discoverable without already
  knowing the shortcut. A discoverability feature that is itself undiscoverable is not a feature.

### P10.4 — Icon consistency

**Problem:** emoji used as icons alongside SVG. All confined to the M8 panels — verified sites:
`🔁` (reminder recurrence), `✓` `⏰` `🗑️` (reminder card buttons ×3, reminder toast buttons ×2),
`📌` (note pin), `🗑️` (note delete, todo list delete), `×` (todo item delete), `🔔` (reminder toast),
`+` (todo add), plus `.jarvis-panel-icon`.

Emoji render at a different optical weight than the 2px-stroke line icons, they are colour-fixed so
they ignore `currentColor`, and they render differently across Windows versions.

**Fix:** move every SVG path into `web/js/icons.js` as named constants, use `icon()` from `dom.js`
(P4.4). Needed: `check`, `clock`, `trash`, `pin`, `close`, `bell`, `plus`, `repeat`, `copy`.
Pull the paths from the ones already inline in `index.html` where they exist (`plus`, `close`, `clock`
are all there) so the set stays visually coherent instead of mixing icon families.

⚠ **TRAP:** `aria-hidden="true"` on every decorative icon, and the **button** carries the `aria-label`.
Today `🗑️` is the button's only content, so a screen reader announces "wastebasket". After the change,
an unlabelled button announces nothing at all — strictly worse. Add `aria-label` to every icon-only
button in the same edit, not as a follow-up.

### P10.5 — Panel position and layout memory

Floating panels (cam, reminders, notes) are draggable but forget everything on reload.

- Persist `{x, y, minimized}` per panel to `localStorage['jarvisPanels']` on drag end (**not** during
  drag — writing `localStorage` per `mousemove` is a synchronous main-thread write per frame).
- Restore on open, **clamped to the current viewport**.
- Snap to edges within 12px.

⚠ **TRAP:** restoring an off-screen position after the user changes monitors or resizes makes a panel
unreachable with no way to recover. **Always clamp** to `[0, innerWidth - panelWidth]` × `[0,
innerHeight - headerHeight]`, and re-clamp on `resize`. Also handle the case where the stored JSON is
corrupt — wrap the parse.

⚠ **TRAP:** `CLAUDE.md` §19 records that `localStorage` was deliberately removed as the source of truth
for the **open conversation** (M12), because two tabs shared one key. Panel geometry is different — it
is per-user preference, not per-tab state, and two tabs agreeing on panel position is *desirable*. But
say so in a comment, or a future reader will think M12's lesson was ignored.

### P10.6 — Toast queue, and stop panels auto-closing

**Toasts:** cap the container at **3** visible, FIFO. Queue the rest; show a "+N more" affordance if
> 3 are waiting. Bursts of proactive notifications currently stack without limit and can cover the
whole right edge.

- `aria-live="polite"` container already exists — keep it. Do **not** make it `assertive`.
- Pause the auto-dismiss timer on hover/focus (a toast that vanishes while being read is a bug).
- Reminder toasts have **actions**, so they must **not** auto-dismiss on a timer at all while focused
  or hovered — 30s is not enough to read and decide.

**Panel auto-close:** `openRemindersPanel()` and `openNotesPanel()` each set a 30-second timer that
closes the panel (`script.js:2617`, `~2820`). Remove them. A panel closing itself while the user is
reading or editing a to-do item is straightforwardly wrong, and the panels already have close buttons.
Keep the timer **only** for the case where the panel was opened by an `action_sink.set_panel()` push
from a tool (i.e. the user did not open it) — pass an `{auto: true}` flag from `handleActions` and
apply the timeout only then, extended to 60s and cancelled on any interaction with the panel.

### P10.7 — Small ones, all confirmed missing

- **Copy button on assistant messages.** Hover-revealed, copies the raw markdown source (keep the raw
  text on the element as a property, not a `data-` attribute — replies can be long).
- **`aria-busy="true"`** on the chat thread while streaming.
- **Focus ring pass:** add `:focus-visible` to every interactive element that lacks one. `.chip`,
  `.action-btn`, `.orb-dash-tab`, `.notes-tab`, `.todo-checkbox` and the reminder/note/todo buttons are
  the likely gaps — grep for `:focus-visible` and compare against the interactive-element inventory.
- **`.todo-checkbox` is a `<div>` with a click handler** (`script.js:~2745`). It is not focusable, not
  keyboard-operable, and announces nothing. Make it a `<button role="checkbox" aria-checked>` or a real
  `<input type="checkbox">` with a styled label. This is a genuine a11y defect, not polish.
- **Scroll anchoring:** if the user has scrolled up mid-stream, do **not** yank them to the bottom on
  every chunk. `scrollToBottom()` currently fires unconditionally. Only auto-scroll when already within
  ~80px of the bottom; otherwise leave them and let the existing scroll FAB (which already appears past
  200px) be the way back. This is the most-noticed streaming annoyance in every chat UI.

### Verify
- Header at 1920, 1280, 768, 480: no overlap, no wrap, no clipped `⋯` menu.
- Palette: `Ctrl+K` opens from idle, from a focused textarea **with text** (must **not** open), from a
  focused textarea **empty** (opens), and **while streaming** (opens, and "Stop generating" is listed).
- Palette keyboard-only: Tab never escapes the dialog, arrows move selection, Enter runs, Escape
  restores focus to the previously focused element.
- Every registered command actually works when run from the palette. Walk all of them.
- `?` overlay lists exactly the commands with shortcuts, plus the four documented manual ones. Every
  listed shortcut works.
- Zero emoji remain in `script.js`/`js/*.js` as UI content: `grep -P '[\x{1F300}-\x{1FAFF}\x{2700}-\x{27BF}]' web/js/*.js` → empty.
- Every icon-only button has an `aria-label`. Check with a screen reader or the accessibility tree.
- Drag each panel, reload → restored. Resize the window very small → panels clamp on screen, still
  draggable. Corrupt `localStorage['jarvisPanels']` manually → no crash, defaults used.
- Fire 6 reminders quickly: 3 visible, "+3 more", none lost, hover pauses dismissal.
- Open the notes panel, start typing in an add-item field, wait 60s: **it does not close.**
- Ask a tool to open the reminders panel: it opens and auto-closes after 60s of no interaction; touch
  it and it stays.
- Scroll up mid-stream: view stays put, FAB appears, clicking it returns to the bottom and resumes
  auto-scroll.
- Keyboard-only pass over the whole app, and a screen-reader pass over the composer, message list,
  history drawer, palette, and both dialogs.
- Re-run P0 perf: **no regression.** The palette must not add an always-on listener beyond one
  `keydown`, and panel-position writes must not appear in a drag profile.

### Rollback
Per sub-phase; commit P10.1–P10.7 separately.

---

## P11 — CROSS-SURFACE CONSISTENCY

**Goal:** Three visual dialects become two intentional ones, and no page is quietly broken.
**Why:** There are **five** UI surfaces and **three** unrelated palettes:

| Surface | Palette | Font | Style source |
|---|---|---|---|
| Chat (`web/index.html`) | `--bg:#050510` `--accent:#7c6aef` `--accent-secondary:#4ecdc4` | Poppins | `web/css/*` |
| Control Center (`app/static/dashboard.html`) | `--bg:#070b12` `--card:#121a2b` `--accent:#3da9fc` `--purple:#7b61ff` | Segoe UI | inline `<style>` |
| Watcher (`app/static/watcher_dashboard.html`) | **identical to Control Center** | Segoe UI | inline `<style>` (duplicated) |
| Viewer (`web/viewer.html`) | `--bg:#0a0a1a` `--surface:#12122a` `--accent:#7c6aef` | (own) | inline `<style>` |
| API Monitor (`web/api-monitor.html`) | reuses chat `style.css` | Poppins | `web/style.css` |

**Chat vs admin being different is a deliberate, good decision** (consumer/ambient vs ops/data-dense) —
`frontend_ui_analysis.md` was right about that. Keep it. Two things are **not** deliberate:

1. The two admin dashboards duplicate their entire theme inline. Verified: both declare the same
   `--bg:#070b12 --bg2:#0b1019 --card:#121a2b --card2:#0f1626 --line:#1e2a40 --txt:#e8eef6
   --mut:#8190a8 --accent:#3da9fc --ok:#3ad17f --bad:#ff6b6f --warn:#f5b942 --shadow:…`. Editing the
   theme means editing two files and hoping.
2. `viewer.html` is a **third** dialect using the chat's `--accent: #7c6aef` with a different
   background and its own everything. It reads as a different product, and it is also **partly broken**:
   it calls `/tasks/{task_id}`, a route that **does not exist** (`CLAUDE.md` §18 issue 5).

### Files
- new: `app/static/admin.css`
- edit: `app/static/dashboard.html`, `app/static/watcher_dashboard.html`
- edit: `web/viewer.html`, `web/api-monitor.html`
- **do not** add a backend route for `/tasks/{task_id}`

### Steps

**P11.1 — Extract `app/static/admin.css`.** Move the shared `:root` block, reset, card grid, pills,
tables, brand mark, and buttons. Link from both dashboards:
```html
<link rel="stylesheet" href="/static/admin.css?v=20260801">
```
Leave genuinely page-specific rules inline, in a `<style>` block with a comment naming what is
page-specific and why.

⚠ **TRAP:** verify the static mount path. `app/static/` is served by FastAPI — check
`app/main.py` / `app/core/startup.py` for the actual mount prefix before writing the `href`. It may be
`/static`, or the dashboards may be returned as raw HTML from `app/api/dashboard.py` with **no static
mount for that directory at all** (`CLAUDE.md` §5 says `/jarvis` and `/app` mount `web/`, and says
nothing about mounting `app/static/`). **If `app/static/` is not mounted, this sub-phase needs a mount
added — which is a backend change beyond the four permitted.** In that case: keep the CSS inline in
**one** dashboard, and have the other `@import` it via the existing HTML-serving route, **or** stop and
ask. Do not add a mount silently.

`--purple:#7b61ff` exists only in `dashboard.html`; keep it in `admin.css` (harmless) and note the
Watcher does not use it.

**P11.2 — Align `viewer.html` to the chat tokens.** It already uses `--accent: #7c6aef`, so the
intent was clearly to match the chat. Finish it: `--bg: #050510`, `--glass-*` from the chat set,
Poppins, `--radius`, `--transition`, `--danger: #ff6b6b`, `--success: #4ecdc4` (both already match).

Keep it **self-contained** (it is opened standalone, possibly outside the app shell) — copy the token
block in with a comment saying it is a copy of `web/css/tokens.css` and must be kept in sync. A copy
with a pointer beats a shared file that breaks a standalone page.

**P11.3 — Fix `viewer.html`'s dead endpoint honestly.**

`GET /tasks/{task_id}` does not exist. Do **not** invent it (§2.1 rule 10 — this is explicitly called
out). The background-task system it was built for was removed; `script.js` has the comment
*"handleBackgroundTasks / pollBackgroundTask / updateTaskCard removed (agent rebuild). Images and
content now arrive inline via `_actions` → `handleActions()`."*

So the viewer's polling path is **vestigial**. Options, in order of preference:

1. **Preferred:** determine whether `viewer.html` is reachable from the running app at all
   (`grep -rn 'viewer' web/ app/`). If nothing links to it, it is dead code from the pre-rebuild
   architecture. **Delete it**, and record the deletion plus the reason in the phase report and in
   `CLAUDE.md` §18 (issue 5 can then be closed).
2. If something *does* link to it: strip the `/tasks/` polling, keep it as a pure content/image viewer
   driven by query params or `postMessage`, and show an honest empty state instead of a request that
   404s forever.

⚠ **TRAP:** do not delete it just because grep finds no link in `web/`. Check `app/api/*.py` for a
route that returns it, and check whether any **tool** emits a URL pointing at it (grep
`viewer` under `app/services/agent/tools/`). A tool emitting a frontend action to open the viewer would
not appear in a frontend grep.

**P11.4 — `api-monitor.html`.** It reuses `web/style.css`, which after P8.4 is a 7-line `@import` shim.
`@import` works, so it will keep functioning — but it will be **serial and slow**. Point it at the
seven `<link>`s directly, or (better) give it just the three it needs (`tokens`, `base`, and its own
small block). Check what it actually uses before trimming; if unsure, link all seven.

**P11.5 — Add `prefers-reduced-motion` and `color-scheme: dark` to all four non-chat surfaces.** P2
only covered the chat app. Same rules apply.

**P11.6 — Cross-surface navigation.** Each admin surface should link back to the chat and to its
siblings — currently you have to know the URLs. One small header link row: `Chat · Control Center ·
Watcher · API Monitor · Health`. Also add these to the P10.2 command palette (already listed there
under "Links").

### Verify
- `/dashboard` and `/watcher` look **identical to their P0 screenshots** (20, 21). This is a pure
  extraction.
- Changing `--accent` in `admin.css` visibly changes **both** dashboards. That is the proof the
  extraction worked.
- `viewer.html`: either gone (with the reason recorded), or loading with chat tokens and **no 404s in
  the Network tab**.
- `api-monitor.html` renders correctly and its CSS loads in parallel, not chained.
- Reduced motion honoured on all five surfaces.
- No white flash on first paint on any surface.
- Nav links work from every surface.
- 215 tests pass — `test_conversation_history_api.py::ConversationDeepLinkTests` in particular, since it
  asserts on the served HTML shell.

### Rollback
`git checkout HEAD~1 -- app/static/ web/viewer.html web/api-monitor.html`

---

## P12 — ACCESSIBILITY AND ASSET VERSIONING PASS

**Goal:** Close the remaining a11y gaps and stop hand-editing cache-bust strings.
**Why:** The existing ARIA is genuinely good — `aria-live` toast regions, `aria-hidden` on panels,
`aria-expanded` on toggles, `role="status"`, an `sr-only` announcer, `role="dialog" aria-modal`,
labelled dialogs. That is better than most hobby projects and better than some commercial ones. The
gaps are specific, not systemic.

Versioning is a separate, small annoyance: `style.css?v=20260725-v2` is hand-bumped, and `script.js` /
`orb.js` have **no** version parameter at all — so they are cached indefinitely and a user can end up
running old JS against new CSS.

### Files
- edit: `web/index.html`, `web/css/*.css`, `web/js/*.js`
- edit: `app/core/middleware.py` *(one additive change, P12.4 — explicitly permitted by §2.1 rule 9)*

### Steps

**P12.1 — Contrast audit.** Check every text/background pair against WCAG 2.1 SC 1.4.3 (AA: 4.5:1 for
normal text, 3:1 for ≥18.66px bold or ≥24px). Use DevTools' contrast readout in the colour picker, or
the Lighthouse accessibility audit.

Known suspects — `--text-dim` (`#8b89a0`-ish) on `--bg` (`#050510`) and on `--glass-bg`:
`.tagline` · `.status-text` · `.msg-label` · `.welcome-sub` · `.settings-hint` · `.activity-step` ·
`.history-item-preview` · `.history-group-label` · `.note-card-time` · `.md-code-head` ·
`.msg-stream-text h4/h5/h6` (P6 sets these to `--text-dim`).

Do **not** raise `--text-dim` globally — the hierarchy it creates is part of the design. Raise it only
where a pair actually fails, and prefer `--text` at a smaller size over a washed-out `--text-dim`.
Record the measured ratios in `docs/UI_BASELINE.md`, both before and after.

⚠ Note honestly in the report: **automated contrast checks cannot see text over the animated star field
or over `backdrop-filter` glass**, because the effective background varies. For those, sample the actual
rendered pixels with a colour picker on a screenshot at a few orb states.

**P12.2 — `body { overflow: hidden }`.** The survey flagged this as breaking keyboard scroll. Verify
before changing: the app is a fixed-height flex layout with `100dvh` and the *chat thread* is the
scroll container, so `overflow: hidden` on `body` is probably correct and intentional. The real question
is whether **`.chat-messages` is keyboard-scrollable** — a `div` with `overflow-y: auto` is only
focusable if it has `tabindex`. Test: Tab to the thread, press Page Down / arrows.

If it does not scroll, the fix is `tabindex="0"` plus `role="log"` and an `aria-label` on
`.chat-messages`, **not** changing `body`. `role="log"` is also the semantically right role for an
append-only message thread and improves screen-reader behaviour during streaming.

⚠ **TRAP:** `tabindex="0"` on a scroll container adds a tab stop before every message's copy button
(P10.7). Make sure the tab order still reads sensibly: thread → composer → actions.

**P12.3 — Remaining a11y items.**
- `aria-live="polite"` + `aria-atomic="false"` on the streaming message so the reply is announced
  progressively rather than re-read from the start on every chunk. ⚠ **Test this with a real screen
  reader** — a badly configured live region on streaming text is *worse* than silence, because NVDA/JAWS
  can re-read the entire message per chunk. If it misbehaves, drop the live region and announce only
  once on completion via the existing `sr-only` announcer. **Record which you chose and why.**
- `prefers-contrast: more` block: solid backgrounds instead of glass, stronger borders. ~20 lines.
- Skip link ("Skip to composer") as the first focusable element, `sr-only` until focused. With 8+ header
  buttons, a keyboard user currently tabs through all of them to reach the textarea.
- `lang="en"` is set — good. Add `lang="hi"` on any Hindi content if the UI ever renders Hindi strings
  (the assistant replies in Hinglish; the *chrome* is English, so this is only about message content —
  note it as unresolvable without language detection and skip it).
- `<title>` is static "J.A.R.V.I.S". Reflect the active conversation title so browser tab switching and
  history are usable: `<conversation title> · J.A.R.V.I.S`. Update it from `setActiveSession()`.
- Verify **every** `aria-hidden` panel also becomes `inert` or has focusable children removed from the
  tab order when closed. A closed panel with `aria-hidden="true"` but still-tabbable buttons is a focus
  trap in reverse — you tab into an invisible panel. **Check every panel.** The `inert` attribute is the
  clean fix and is well supported now.

⚠ **TRAP:** the panels use `aria-hidden` + a CSS transform to hide, not `display: none`. That means
their buttons **are** still focusable today. This is likely a real existing bug — test it before
assuming otherwise by tabbing repeatedly from the header with all panels closed.

**P12.4 — Content-hash versioning.** Replace hand-bumped `?v=` strings.

Smallest honest option that needs **no build step** (§2.1 rule 2): have the server stamp a version
derived from file mtimes and inject it. But `index.html` is served by `StaticFiles`, not a template, so
injection means adding a template route — more machinery than this deserves.

**Chosen approach — a single manual constant, but only one:**
```html
    <!-- ONE version string for every asset. Bump this line and nothing else
         when you change any CSS or JS. Previously style.css had ?v=20260725-v2
         and script.js/orb.js had NO version at all, so a user could run stale
         JS against fresh CSS.  [M14 P12.4] -->
```
and use the identical `?v=YYYYMMDD-N` on **all** seven CSS links and the JS module. Add it to the
phase-report checklist and to `CLAUDE.md` §13 so it is not forgotten.

**Plus** the backend half, which is the part that actually makes this safe — in
`app/core/middleware.py` (`TimingMiddleware` already sets cache headers, so this is a one-line change
in an existing code path):
```python
# Versioned static assets (?v=...) are immutable by construction — the URL
# changes when the content changes. Unversioned ones must revalidate, or a
# forgotten ?v= bump silently serves stale JS forever.  [M14 P12.4]
if request.url.path.startswith(("/jarvis/", "/app/", "/static/")):
    if request.url.query and "v=" in request.url.query:
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        response.headers["Cache-Control"] = "no-cache"
```
`no-cache` means "revalidate", not "don't cache" — a 304 is cheap on localhost. This makes a forgotten
version bump a non-event instead of a mystery bug.

⚠ **TRAP:** read the existing cache-header logic in `middleware.py` before editing. If it already sets
`Cache-Control` for these paths, **modify** that branch rather than adding a second one that fights it.
`index.html` itself must **never** be `immutable` — it carries the version strings.

**P12.5 — `<meta name="description">` and a favicon.** `showReminderToast` references
`/web/favicon.ico` (`script.js:~2960`) — a path that is almost certainly wrong (the mount is `/jarvis`,
not `/web`), so native reminder notifications are showing a broken/default icon. Add a real
`web/favicon.ico` (or an inline SVG favicon, no binary asset needed) and fix the path to resolve
through `<base href>`.

### Verify
- Lighthouse accessibility score on `/jarvis/`: record before and after. Aim ≥ 95, and **explain every
  remaining flag** rather than chasing the number.
- Every contrast pair from P12.1 measured, recorded, and passing — or documented as a deliberate
  exception with the ratio stated.
- Keyboard-only: complete a full task (open history → select a conversation → send a message → stop it →
  open reminders → create → mark done → open the palette → run a command) **without touching the mouse.**
- With all panels closed, Tab from the top of the page through to the composer: focus must **never** land
  on an invisible element. This is the `inert` check.
- Screen reader (NVDA on Windows): the composer is labelled, a streaming reply is announced sensibly
  (not re-read per chunk), both dialogs announce their titles, toasts announce once, the palette
  announces its result count.
- Reduced motion, reduced transparency, `prefers-contrast: more`, and 200% browser zoom all usable
  (200% zoom without horizontal scrolling is WCAG 1.4.4/1.4.10).
- Native reminder notification shows the real icon.
- Browser tab title reflects the open conversation and updates when you switch.
- Network: a versioned asset returns `immutable`; `index.html` does not.
- 215 tests pass.

### Rollback
`git checkout HEAD~1 -- web/ app/core/middleware.py`

---

## 4. DONE CRITERIA FOR M14

The milestone is complete when **all** of these hold:

| # | Criterion |
|---|---|
| 1 | `docs/UI_BASELINE.md` has before/after numbers for every P0.3 measurement, and every §2.3 target is met or the miss is explained with a number |
| 2 | Idle CPU with the tab **hidden** is effectively zero |
| 3 | A killed stream leaves **no** blinking cursor, **one** bubble, a clear incomplete marker, and a working retry |
| 4 | The user can stop a reply |
| 5 | Markdown renders correctly **during** streaming with no flashing, and code blocks do not widen the bubble |
| 6 | The XSS probes in P4's verify block all fail to execute |
| 7 | One SSE consumer: `grep -c 'res.body.getReader' web/js/*.js` → 1 |
| 8 | No CSS rule is silently overridden by a duplicate (P1) |
| 9 | Every `z-index` comes from the scale; a modal is never covered |
| 10 | `prefers-reduced-motion` is honoured by every animation and transition |
| 11 | Command palette and shortcut overlay both render from `commands.js` — no hand-maintained list |
| 12 | Zero console errors/warnings on load and through a full feature pass |
| 13 | `/jarvis/`, `/app/`, and a hard refresh at `/jarvis/c/<id>` all work |
| 14 | 215 tests pass; `scripts/_history_ui_check.py` passes |
| 15 | No new runtime dependency, no build step, no `package.json` |
| 16 | `CLAUDE.md` §13 updated to describe the new frontend layout |

### `CLAUDE.md` updates required at the end

`CLAUDE.md` is the single source of context for every agent in this repo and it currently describes a
frontend that will no longer exist. Update:

- **§4** directory tree: add `web/css/` and `web/js/`, remove `script.js` / `style.css`
- **§13** rewrite the frontend table: new files, new line counts, the module map, `markdown.js`,
  `commands.js`, `bus.js`
- **§13** new subsections: *Stream failure handling*, *Markdown rendering*, *Command palette*,
  *Z-index scale*, plus the "things that are easy to break here" traps from each phase — that section
  format is already established for the history drawer and URL routing, and these traps belong in it
- **§16** testing: the `node --check` command changes for ES modules
- **§18** known issues: close #4 (CSS duplication) and #5 (`viewer.html` dead route); add anything found
  and deliberately deferred
- **§19** decision log: add the decisions this plan made, specifically —
  *no framework reaffirmed* · *hand-written markdown renderer over marked+DOMPurify* ·
  *`<link>` over `@import` for the CSS split* · *toasts below modals* ·
  *retry re-sends rather than resuming* · *CSP deferred* · *no syntax highlighting* ·
  *`viewer.html` deleted or retained, with the reason*

---

## 5. IF YOU HAVE LIMITED TIME

| Time | Do |
|---|---|
| **1 hour** | P1 |
| **Half a day** | P1, P2, P3 |
| **One day** | P1, P2, P3, P4, P5 |
| **Two days** | add P6, P7 |
| **A week** | everything through P12 |

P1 alone is worth more than P9 and P10 combined, because until it lands, half the stylesheet cannot be
edited at all.

---

## 6. WHAT THIS PLAN DELIBERATELY DOES NOT DO

Recorded so the next agent does not "helpfully" add them:

| Not doing | Why |
|---|---|
| Frontend framework | `CLAUDE.md` §19 decision; a single chat page does not justify it |
| Build step / bundler / `package.json` | ES modules are served natively; a toolchain is a maintenance liability on a personal machine |
| `marked` / `DOMPurify` / `highlight.js` | P6's renderer is smaller than the two of them, and XSS-safe by construction rather than by sanitisation |
| Syntax highlighting | Needs a grammar per language and burns CPU per frame on the biggest blocks |
| Light theme | User runs dark; a second theme doubles every visual test |
| Content-Security-Policy | Backend change, and `style-src 'unsafe-inline'` would be required today, so it would be weak. Own milestone. |
| Virtual scrolling for the chat thread | Conversations are tens of messages, not thousands. `content-visibility` (P2) handles it. Revisit only if a real transcript gets slow. |
| Stream resume after disconnect | No backend resume endpoint; the server has already accumulated a partial assistant message. Real design work, not UI work. |
| LLM-generated conversation titles | Already listed as a deferred history follow-up in `CLAUDE.md` §18 |
| SQLite FTS index for history | Same — 44 conversations is a directory scan (`CLAUDE.md` §19) |
| Voiced reminder notifications | On the roadmap (`CLAUDE.md` §18) but it is a **backend** TTS-pipeline change, not UI |
| Onboarding tour / analytics / plugin system / theme marketplace | Not asked for (§2.1 rule 5) |
| Auth on the frontend | User explicitly declined (`CLAUDE.md` §18 issues 2–3) |

---

## 7. PHASE REPORT TEMPLATE

Produce one of these per phase. Paste into a single `docs/M14_REPORT.md`, appending as you go.

```markdown
## Pxx — <name>
**Commit:** <sha>
**Files changed:** <list with +/- line counts>

### What changed
<2–5 sentences. What a reader needs to know, not a diff summary.>

### Measurements
| Metric | Before | After | Target | Met? |
|---|---|---|---|---|

### Verification
- [ ] every item from the phase's Verify block, ticked
- Static checks: node --check ✓ / compileall ✓ / pytest 215 ✓

### Deviations from the plan
<Anything you did differently, and WHY. If the plan was wrong, say so — the
plan being wrong is expected and useful information. Do not silently diverge.>

### Traps hit
<Which ⚠ TRAP notes were real, and any NEW traps you found. New ones must also
be added to CLAUDE.md §13 if they are load-bearing.>

### Left undone
<Anything deferred, and what would need to be true to do it.>
```

---

## 8. SOURCES

External references consulted for this plan. All technical claims about **this codebase** were
verified directly against the working tree; the links below informed general technique only.

- [Streamdown — unterminated block parsing](https://vercel-streamdown.mintlify.app/advanced/termination) — why fence detection needs structural line-by-line analysis rather than counting markers
- [Streamdown — streaming architecture](https://vercel-streamdown.mintlify.app/features/streaming) — block-level splitting and per-block memoisation, the basis of P6's D1
- [Streamdown overview](https://reactlibs.dev/articles/streamdown/) — the "repair unterminated markdown before rendering" preprocessing approach
- [MDEx — streaming markdown](https://mdex.hexdocs.pm/streaming.html) — the failure modes of naive per-chunk parsing
- [Hyperframes — backdrop-filter performance](https://hyperframes.heygen.com/guides/performance) — blur cost scales with area and radius, and stacked layers multiply (P2.5)

*Content from these sources was rephrased and summarised for compliance with licensing restrictions.*

---

**END OF PLAN**
