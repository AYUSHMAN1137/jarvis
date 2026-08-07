# M14 UI Milestone - Implementation Report

**Plan:** `IMPLEMENTATION_PLAN_UI_M14.md`
**Status:** all phases P0 through P12 implemented and verified
**Asset version shipped:** `20260804-p12`
**Last git commit in the tree:** `d5acc03` (P7 onward is intentionally uncommitted - see section 10)

---

## 0. How to read this document

This report has three audiences and they need different parts of it.

| If you are... | Read |
|---|---|
| **You (owner), just want it running** | Section 6, then section 7 |
| **Your AI IDE (Cursor / Claude Code / Copilot)** | Section 5, then section 8. Section 8 is written to be pasted in as rules. |
| **Anyone asking "why is it like this?"** | Sections 2, 3 and 4 |

Every number in this document was measured in the working tree, not estimated.
Where a measurement contradicted the plan, the measurement won and the
contradiction is written down rather than quietly smoothed over.

---

## 1. One-paragraph summary

The frontend was one 3583-line script and one 4470-line stylesheet with 48
duplicated CSS blocks. It is now 24 ES modules (7635 lines) and
six stylesheets (5230 lines) with a documented, load-bearing cascade
order. Along the way: the orb stopped burning GPU when hidden, the markdown
renderer stopped breaking on half-streamed code fences, a command palette and
keyboard shortcuts were added, 36 invisible keyboard traps were removed, the
five separate surfaces were given one visual language, dead code that fired
120 failing network requests per visit was removed, and asset caching was made
correct in both directions. **No feature was removed and no behaviour was
changed that the user could see, except where the old behaviour was a bug.**

---

## 2. The five rules this work followed

These matter more than any individual diff, because they are why the result
can be trusted.

1. **Measure before changing.** Every phase started with a measurement and
   ended with the same measurement repeated. A refactor that cannot show a
   before and an after is a rewrite with extra steps.
2. **When a safety gate fires, check the premise before weakening the gate.**
   This happened twice, and both times the plan was wrong and the gate was
   right. Details in P11.1 and P12.1.
3. **Gate on behaviour, never on source text.** A verification that greps for
   a string fails the moment someone writes a comment explaining the string.
   This cost one wasted script; it is now a standing rule.
4. **A deviation from the plan gets written down, never done silently.** Three
   real deviations are recorded in section 4 and in `CLAUDE.md` section 19.
5. **State what could not be verified.** Three things could not run in the
   build environment. They are listed in section 6.4 with the exact commands
   for you to run them. They are not claimed as passing.

---

## 3. What was wrong before, in plain language

| Problem | What it actually did to you |
|---|---|
| One 3583-line `script.js` | Any change risked everything. Your AI IDE had to load the whole file to change one function. |
| 48 duplicated CSS blocks | Editing a colour changed it in one place and not the other, apparently at random. |
| Orb rendered at full DPR, always | Fan spun on a background tab. Battery drain with the window not even visible. |
| Markdown parsed per chunk | A half-streamed code fence rendered as garbage, then re-rendered correctly. Visible flicker on every code answer. |
| 36 focusable controls in closed panels | Tabbing from the header disappeared into invisible panels. Keyboard use was effectively broken. |
| `viewer.html` polled a deleted route | Every visit: ~120 failed requests over 2 minutes, then a false "Task timed out. Please try again." Retrying could never work. |
| Notification icon pointed at `/web/favicon.ico` | The app is mounted at `/jarvis`, and no `.ico` ever existed. Every reminder notification showed the browser's default icon. |
| Three different cache-busting strings | Some assets updated on deploy and some did not, so you saw a half-old UI and could not tell. |
| Five surfaces, five visual languages | The dashboards looked like a different product from the chat. |

---

## 4. Phase by phase - what was done and why

### P0 - Measurement harness

`web/js/devtools.js` was added: an in-page probe that reports body element
count, computed styles, stylesheet list, command registry state, panel state
and focus reachability. Every later phase is verified with it.

**Why first:** without a fingerprint, "the refactor changed nothing visible"
is an opinion. With one, it is a number. The authoritative fingerprint is
**body elements = 380, 11780 computed rows** taken before any change.

### P1 - CSS duplication

48 duplicate rule blocks deleted; **14 kept**, each with a comment saying why
it is a deliberate override and not a duplicate. Body-element fingerprint
identical before and after.

**Why the 14 stayed:** a later rule that overrides an earlier one on purpose
looks exactly like a duplicate to a script. Deleting those would have changed
rendering. Each one now says so in a comment, so the next person does not have
to re-derive it.

### P2 - Motion and GPU cost

- `prefers-reduced-motion` honoured for the star drift and orb transitions.
- `color-scheme: dark` declared, so form controls and scrollbars stop being
  light-themed islands.
- Orb: device-pixel-ratio capped at **1.25**, pauses on `visibilitychange`,
  pauses when an `IntersectionObserver` says it is covered, `_adapt(dt)` drops
  quality under load, and `destroy()` releases the WebGL context.

**Why the DPR cap:** at DPR 2 the orb renders four times the pixels for a
difference nobody can see on an ambient background shape.

### P3, P4, P5 - Layout, chat, panels

Spacing scale, responsive behaviour, message layout, panel chrome. These are
the phases with the least to explain: they are the plan's visual corrections,
applied and fingerprinted.

### P6 - Streaming-safe markdown

The renderer was rewritten to analyse structure line by line and repair
unterminated blocks before rendering, instead of parsing each streamed chunk
independently. It has its own suite: **41 tests, all passing**
(`node web/js/markdown.test.mjs`).

**Why it matters:** an unterminated code fence is the normal state of a
streaming response, not an error case.

### P7 - Visual polish pass

Applied and fingerprinted; the body-only fingerprint
`1653324858 / 380 / 11780` was identical before and after, which is the proof
that the polish was purely visual.

### P8 - `style.css` split into six

Order: **tokens, base, layout, chat, panels, floats**. Linked as six parallel `<link>` tags, deliberately
not `@import`.

**Why not `@import`:** it is serial. The browser cannot discover the second
file until the first has been fetched and parsed. Six `<link>`s download in
parallel.

**Why this exact order:** the cascade is the API. `tokens` must come first
because everything reads its custom properties; `floats` must come last
because it deliberately overrides `panels`. Sorting these alphabetically -
which looks like tidying - silently changes rendering.

| # | File | Lines |
|---|---|---:|
| 1 | `web/css/tokens.css` | 75 |
| 2 | `web/css/base.css` | 568 |
| 3 | `web/css/layout.css` | 1069 |
| 4 | `web/css/chat.css` | 643 |
| 5 | `web/css/panels.css` | 1121 |
| 6 | `web/css/floats.css` | 1754 |

### P9 - `script.js` split into modules

24 ES modules, 7635 lines total, no file over ~900 lines.

| Module | Lines | Purpose |
|---|---:|---|
| `web/js/api.js` | 84 | fetch helpers, base URL, error shaping |
| `web/js/bus.js` | 61 | tiny pub/sub - how modules talk without importing each other |
| `web/js/chat.js` | 858 | composer, SSE stream parsing, message rendering, activity events |
| `web/js/commands.js` | 331 | Ctrl+K command palette and the command registry |
| `web/js/config.js` | 210 | constants and selectors - a LEAF: it imports nothing |
| `web/js/devtools.js` | 86 | in-page measurement probe used to verify each phase |
| `web/js/dom.js` | 84 | element lookups and small DOM utilities - LEAF |
| `web/js/geometry.js` | 179 | panel drag, resize, snapping, viewport clamping |
| `web/js/headermenu.js` | 107 | header overflow menu for controls that no longer fit |
| `web/js/history.js` | 634 | conversation drawer, session switching, document title |
| `web/js/icons.js` | 96 | inline SVG icon set - LEAF |
| `web/js/main.js` | 346 | the ENTRY. Boots everything. Nothing may import it. |
| `web/js/markdown.js` | 869 | streaming-safe markdown renderer (own test suite) |
| `web/js/notes.js` | 340 | notes and to-do panel |
| `web/js/notifications.js` | 205 | toasts and native notifications |
| `web/js/orb.js` | 731 | WebGL orb renderer - LEAF |
| `web/js/orbctl.js` | 390 | orb state machine: which visual state maps to which app state |
| `web/js/panels.js` | 760 | panel open/close, overlay, and the inert/aria-hidden sweep |
| `web/js/reminders.js` | 208 | reminders panel |
| `web/js/shortcuts.js` | 183 | keyboard shortcuts and the shortcuts sheet |
| `web/js/state.js` | 55 | shared mutable app state - LEAF, imports nothing |
| `web/js/util.js` | 70 | formatting and timing helpers |
| `web/js/vision.js` | 211 | camera capture panel |
| `web/js/voice.js` | 537 | push-to-talk, speech recognition, speech synthesis |

**Three rules make this graph safe, and breaking any one of them produces a
`Cannot access X before initialization` error at boot:**

1. **Nothing imports the entry.** `main.js` imports everything; if a module
   imports `main.js` you get a cycle and a dead app.
2. **Leaf modules import nothing.** `config.js`, `state.js`, `dom.js`,
   `bus.js`, `icons.js`, `orb.js`. A leaf that grows an import stops being a
   leaf and the cycle appears somewhere unrelated.
3. **Cross-module talk goes through `bus.js`**, not through imports, whenever
   the two modules would otherwise import each other.

### P10 - Interaction layer

- Header overflow menu (controls that no longer fit stop being lost).
- **Command palette (Ctrl+K)** with 16 commands: `camera.start`, `camera.stop`, `chat.focus`, `chat.new`, `chat.stop`, `history.toggle`, `links.dashboard`, `links.health`, `links.monitor`, `links.watcher`, `notes.open`, `orb.dashboard`, `panels.activity`, `panels.search`, `panels.settings`, `reminders.open`
- Shortcuts sheet, inline SVG icon set, toast queue with auto-close,
  scroll anchoring, copy buttons, `aria-busy` during streaming, and real
  checkbox buttons instead of divs pretending to be checkboxes.

### P11 - One visual system across five surfaces

**The plan's premise was wrong, and this is the most useful finding in the
milestone.** The plan said the two dashboards duplicate their entire theme.
The extraction script's own sanity gate refused to finish. Measured instead:

| Measurement | Control | Watcher |
|---|---:|---:|
| CSS chunks | 59 | 69 |
| Selectors in both | 18 | 18 |
| ...identical bodies | **6** | 6 |
| ...different bodies | **12** | 12 |
| Verbatim shareable | **342 bytes** | - |

So the extraction has two rules: identical rules move verbatim, and `:root`
merges as a superset. `:root` is safe to merge because an unused custom
property is inert; merging `.card` or `.pill` would have restyled both pages.

**Proof, not assumption:** `--accent` in the shared sheet was changed to
`#ff00ff`, both dashboards were re-measured and both moved, then it was
reverted and both returned. A `<link>` tag existing proves nothing.

**Deviation - `app/static/` is not mounted.** A stylesheet placed there would
have 404ed. One additive route was added to the existing dashboard router:
`GET /static/admin.css` returning a `FileResponse`. A `StaticFiles` mount was
deliberately avoided - it would expose both dashboard HTML files at a second
URL with different behaviour.

**Deviation - `viewer.html` was repaired, not deleted.** The plan allowed
deletion if nothing reached it. Nothing links to it, but it lives inside the
`/jarvis` mount, so a bookmark still works and grep cannot see bookmarks. Its
dead polling loop was removed; it now reads `?image=<https url>` or `?text=`,
or accepts a same-origin `postMessage`, validates image URLs against
`^https?://` so a `javascript:` URL cannot reach `src`, and says plainly when
it has nothing to show. Its tokens are a **documented copy** of the chat
tokens, not a shared file, because it must render with the app down.

**Navigation:** the four admin-ish surfaces get a visible nav row with
`aria-current="page"`. The chat deliberately does not - it is the surface that
stays open all day, and permanent header links used once a week are how a
header rots. It reaches the others through four palette commands that open a
new tab, so a reply in flight is never abandoned.

### P12 - Accessibility, caching, metadata, cleanup

**Contrast was measured and already passed.** With proper alpha compositing
against both the page and the glass:

| Token | On page | On glass | AA needs |
|---|---:|---:|---:|
| `--text` | 17.41:1 | 17.01:1 | 4.5 |
| `--text-dim` | **5.31:1** | 5.31:1 | 4.5 |
| `--accent` | 4.99:1 | 4.86:1 | 4.5 |
| `--accent-secondary` | 10.48:1 | 10.22:1 | 4.5 |
| `--text-muted` | 2.35:1 | 2.39:1 | 4.5 |

All 13 selectors the plan flagged use `--text-dim` and **pass**, so nothing
was recoloured. `--text-muted` stays below AA on purpose - placeholders,
timestamps, disabled hints - and `prefers-contrast: more` lifts it.

**The thread was not keyboard-scrollable.** `body { overflow: hidden }` was
checked and kept: this is a fixed-height `100dvh` layout where the thread
scrolls, not the page. The real defect was a scroll container with no
`tabindex`. It now has `tabindex="0"`, `role="log"`, `aria-live="polite"` and
a `:focus-visible` ring that does not fire on mouse clicks.

**Closed panels were a reverse focus trap: 36 reachable controls, now 0.**

> The first implementation decided "is this panel visible" from geometry and
> was thrown away after measurement. Panels slide for ~250ms, so geometry says
> "off-screen" *while the panel is opening* - and `openHistoryPanel()` focuses
> its search box immediately. Focus into an `inert` subtree is refused, so the
> "fix" would have silently broken a working keyboard path. Openness now comes
> from app state (`.open` / `.visible` / `aria-hidden`), which is true on the
> first frame. The same sweep also repairs `aria-hidden` on the three panels
> that only ever set a class, so the two signals cannot drift apart.

**Caching.** All six stylesheets and the JS entry carry one version string,
`20260804-p12`. The existing middleware was extended, not duplicated: an asset
with `?v=` gets `public, max-age=31536000, immutable`; everything else gets
`no-cache, must-revalidate`. **HTML is never immutable** - it carries the
version strings, and caching it for a year ships an app that cannot update
itself.

**Also:** `<meta name="description">`, an inline-SVG favicon
(`web/favicon.svg`), a fixed notification icon, a tab title of
`<conversation> - J.A.R.V.I.S` (conversation first, because tab strips
truncate from the right), a skip link that is genuinely the first focusable
element, decorative emoji hidden from screen readers, and the deletion of the
`web/style.css` shim after all ten remaining references were inspected by hand
(nine prose comments, one stale file list, zero live consumers).

---

## 5. The system as it stands now

### 5.1 Surfaces

| Surface | URL | Served from | Notes |
|---|---|---|---|
| Chat | `http://127.0.0.1:8000/jarvis/` | `web/index.html` | The main app. No nav row by design - Ctrl+K instead. |
| Control Center | `http://127.0.0.1:8000/dashboard` | `app/static/dashboard.html` | Admin overview of every phase. |
| Watcher | `http://127.0.0.1:8000/watcher` | `app/static/watcher_dashboard.html` | Live system state. |
| API Monitor | `http://127.0.0.1:8000/jarvis/api-monitor.html` | `web/api-monitor.html` | API key usage. |
| Health | `http://127.0.0.1:8000/health` | `app/api/system.py` | JSON health probe. |
| Viewer | `http://127.0.0.1:8000/jarvis/viewer.html?text=hello` | `web/viewer.html` | Standalone image/text viewer. |

### 5.2 Frontend layout

```
web/
  index.html          564 lines - app shell. Contains the load order. Read the comments.
  favicon.svg         inline-SVG icon, also used by native notifications
  viewer.html         708 lines - standalone, must render with the app down
  api-monitor.html    links the six chat stylesheets directly
  api-monitor.js
  css/                six files, order is load-bearing: tokens, base, layout, chat, panels, floats
  js/                 24 ES modules, entry is main.js
app/static/
  admin.css           shared admin theme, served by a route (NOT a mount)
  dashboard.html
  watcher_dashboard.html
```

### 5.3 Contracts that must not be broken

| Contract | Rule | What breaks if ignored |
|---|---|---|
| CSS order | `tokens, base, layout, chat, panels, floats` | Silent visual regressions; `floats` must override `panels` |
| Module graph | Nothing imports `main.js`; leaves import nothing | `Cannot access X before initialization` at boot, blank page |
| Panel inert sweep | Openness comes from state, never geometry | Keyboard focus breaks during the open animation |
| Asset version | One string for all assets, bumped together | Half-updated UI that nobody can diagnose |
| HTML caching | HTML is never `immutable` | App can never update itself |
| Viewer tokens | A documented copy, not an import | Viewer breaks exactly when the app is down |
| `<base href="/jarvis/">` | Load-bearing | `/jarvis/c/<id>` deep links 404 their own CSS |

---

## 6. What YOU need to do

Nothing about your environment changes. **No `.env` edits, no new dependencies,
no database migration, no new config keys.** This milestone touched the
frontend, one middleware function and one route.

### 6.1 Install

1. Back up your current folder (or just keep the previous zip).
2. Unzip this zip over it, replacing files.
3. Confirm `web/style.css` is **gone** and `web/favicon.svg` is **present**.
   Both are expected.
4. Your `credentials.json`, `.env`, `data/` and `config.py` are untouched.

### 6.2 Run

```
python run.py
```

Then open **http://127.0.0.1:8000/jarvis/**

**Do a hard refresh the first time: Ctrl+Shift+R.** Your browser is holding
the old CSS, and the new cache headers only apply to what it fetches next.

### 6.3 Five-minute acceptance check

| # | Do this | You should see |
|---|---|---|
| 1 | Load the chat | Orb, glass UI, no flash of unstyled text |
| 2 | Press **Ctrl+K** | Palette with 16 commands, including a `Links` group |
| 3 | Press **Tab** from the very top | "Skip to the message box" appears - it is invisible until focused |
| 4 | Keep pressing **Tab** | Focus stays on visible controls. It must never vanish. |
| 5 | Ask something that returns code | Code block renders cleanly while streaming - no flicker or garbage |
| 6 | Click the thread, press arrow keys / PageDown | The conversation scrolls |
| 7 | Open a conversation from history | Tab title becomes `<conversation> - J.A.R.V.I.S` |
| 8 | Switch to another browser tab for a minute | Fan quiets. Orb is paused. |
| 9 | Open `/dashboard` and `/watcher` | Same colours, nav row on both, current one marked |
| 10 | Open `http://127.0.0.1:8000/jarvis/viewer.html` | "Nothing to show" message - not a spinner, not a timeout |
| 11 | Let a reminder fire | Native notification shows the JARVIS orb icon |

If all eleven behave, the milestone landed correctly.

### 6.4 The three things I could NOT run - please run them

The build environment had no network, no pytest and no FastAPI installed, so
these are **not** claimed as passing. Commands for your machine:

**1. Your test suite** (the plan refers to 215 tests)
```
pytest -q
```
*Expected:* same result as before this milestone. Nothing in M14 touches
backend logic except one middleware function and one added route, so a new
failure means something regressed - report it.

**2. Lighthouse** (Chrome DevTools > Lighthouse > Desktop > Accessibility +
Performance, on `http://127.0.0.1:8000/jarvis/`)
*Expected:* accessibility in the mid-to-high 90s. The known deductions are
decorative-contrast items on the animated background, which cannot be measured
statically and were left alone deliberately.

**3. A screen reader pass** (NVDA on Windows, free)
*Expected:* Tab reaches the skip link first; closed panels are never
announced; streaming replies are announced as additions, not re-read whole.

### 6.5 Two things you may want to change, both one-liners

| Want | Where |
|---|---|
| `--text-muted` a bit brighter everywhere | `web/css/tokens.css`, raise the alpha from `0.28` |
| A different tab-title separator | `web/js/history.js`, `BASE_TITLE` and `setDocumentTitle()` |

---

## 7. What good looks like - reference outputs

So you can tell "working" from "looks fine but is broken":

```
node web/js/markdown.test.mjs        ->  RESULT 41/41 passed
python -m compileall -q app run.py   ->  (silent = OK)
```

The in-page probe (P0 harness) on a healthy boot reports:

```
skip link is first focusable         true
panels seen / inert when closed      8 / 8
focus leaks into closed panels       []
thread role / live                   log / polite
assets on one version                7 assets, 1 distinct version
favicon fetch                        ok
```

Two console messages are **expected** when the backend is not running and are
not bugs: `Failed to start startup brief` and `History load failed: HTTP 404`.

---

## 8. Rules for your AI IDE

**Paste this section into your IDE's rules file** (`.cursorrules`, `CLAUDE.md`
project rules, or the equivalent). It is written as instructions, not prose.

```
PROJECT: J.A.R.V.I.S frontend, post-M14.

ARCHITECTURE FACTS
- web/js/ holds 24 ES modules. Entry point: web/js/main.js.
- web/css/ holds six stylesheets. Load order is tokens, base, layout, chat, panels, floats and is
  load-bearing. It is declared in web/index.html with a comment saying so.
- Current asset version string: 20260804-p12

HARD RULES - breaking these produces a blank page or a silent visual bug
1. NOTHING may import main.js. main.js imports everything else.
2. These are leaf modules and must import nothing: config.js, state.js,
   dom.js, bus.js, icons.js, orb.js. If a leaf needs an import, the design is
   wrong - use bus.js instead.
3. If two modules would import each other, they communicate through bus.js.
4. Never reorder or alphabetise the six stylesheet links.
5. Never add @import to the CSS. Use a parallel <link> in index.html.
6. Panel visibility for accessibility is decided from STATE (.open /
   .visible / aria-hidden), never from geometry or getBoundingClientRect.
   Panels animate for ~250ms and geometry lies during that window.
7. HTML responses must never be cached immutably. Only ?v= assets are.
8. web/viewer.html must keep its own copy of the design tokens. Do not
   refactor it to import them - it must render when the app is down.
9. Do not remove <base href="/jarvis/"> from index.html.
10. app/static/ is NOT mounted. Files there need an explicit route in
    app/api/dashboard.py.

WHEN YOU CHANGE ANY FILE IN web/css/ OR web/js/
- Bump the version string on every asset link in web/index.html together.
  They must all stay identical. Grep: ?v=

HOW TO ADD A NEW PANEL
1. Markup in web/index.html, class "jarvis-panel", with aria-hidden="true".
2. Add its selector to INERT_PANEL_SELECTOR in web/js/config.js.
3. Styles go in web/css/panels.css (or floats.css if it floats above panels).
4. Open/close through web/js/panels.js so the inert sweep sees it.
5. Verify: with the panel closed, Tab must never reach anything inside it.

HOW TO ADD A COMMAND TO THE PALETTE
- Register it in registerAppCommands() in web/js/main.js. Give it an id of
  the form "group.action". It appears in Ctrl+K automatically.

HOW TO ADD A NEW JS MODULE
1. Create web/js/<name>.js with named exports.
2. Import it from main.js. Do not import main.js from it.
3. Keep it a leaf if you can.

VERIFICATION - run before claiming a change works
  for f in web/js/*.js; do node --experimental-detect-module --check "$f"; done
  node web/js/markdown.test.mjs      # expect RESULT 41/41 passed
  python -m compileall -q app run.py config.py scripts

STYLE OF WORK EXPECTED
- Measure before and after any refactor. "It looks the same" is not evidence.
- If a check fails, verify the premise before weakening the check.
- Never gate a verification on source text (grep for a string). Gate it on
  behaviour. A comment mentioning the string will defeat a text gate.
- Do not silently deviate from an instruction. Do it differently and say why.

DOCUMENTATION DUTY
- CLAUDE.md section 13 describes the frontend. Update it in the same change,
  not later. Non-obvious reasoning goes in CLAUDE.md section 19.
```

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| UI looks half-old after update | Browser cached the old CSS | Ctrl+Shift+R once |
| Blank page, console says `Cannot access X before initialization` | A module import cycle - usually something now imports `main.js`, or a leaf grew an import | Remove that import; use `bus.js` |
| A control is unreachable by Tab | It is inside a panel the state says is closed | Fix the panel's state, not the inert sweep |
| Panel opens but its search box is not focused | Something reintroduced geometry-based inert logic | Revert to state-based |
| Dashboards lose their colours | `/static/admin.css` route missing or renamed | Check `app/api/dashboard.py` |
| Deep link `/jarvis/c/<id>` is unstyled | `<base href>` removed from `index.html` | Put it back |
| Notification shows the browser icon | `web/favicon.svg` missing from the deploy | Ship the file |
| Code blocks flicker while streaming | Something bypassed `markdown.js` | Route rendering back through it |

---

## 10. Git, checkpoints, rollback

Commits were **paused at your request** partway through. Phases P7 onward are
in the working tree but uncommitted - the last commit is `d5acc03`. When you
are happy with this build:

```
git add -A
git commit -m "M14: UI milestone P0-P12 - modular frontend, a11y, caching"
```

Rollback is per-zip: each checkpoint zip you were sent is a complete, working
tree at that phase. `credentials.json` and `data/` are yours and were never
modified.

---

## 11. Deliberately left undone

| Item | Why |
|---|---|
| Light theme | You run dark. A second theme doubles every visual test. |
| Content-Security-Policy | Needs `style-src 'unsafe-inline'` today, so it would be security theatre. Own milestone. |
| Virtual scrolling in the thread | Conversations are tens of messages. `content-visibility` already handles it. |
| Stream resume after disconnect | Needs a backend resume endpoint. Real design work, not UI work. |
| LLM-generated conversation titles | Backend feature, already on the roadmap. |
| Contrast over the star field / glass blur | No constant background, so no static tool can measure it. Needs a screenshot sample. |
| Nav row on the chat surface | Deliberate. Ctrl+K instead. |

---

## 12. Related documents

| File | What it holds |
|---|---|
| `IMPLEMENTATION_PLAN_UI_M14.md` | The original plan |
| `docs/UI_BASELINE.md` | Running record: measurements per phase, in order |
| `docs/M14_REPORT.md` | Per-phase report in the plan's template |
| `CLAUDE.md` s13 | Frontend architecture - the file your IDE should read first |
| `CLAUDE.md` s19 | Decision log - reasoning not derivable from the code |
| **this file** | Narrative report plus your runbook |
