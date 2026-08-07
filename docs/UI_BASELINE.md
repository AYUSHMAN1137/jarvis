# UI Baseline - M14

**Rollback target:** the `M14 baseline` commit at the root of this branch (`git log --oneline | tail -1`).
Every phase after P0 commits separately, so `git reset --hard <that SHA>` undoes the entire milestone.

---

## 0. Environment this baseline was taken in

The M14 plan (P0.2, P0.3) asks for DevTools CPU profiles, Chrome Task Manager reads and 23
screenshots taken against a running backend. **That was not possible in the environment this work
was executed in**, and it is recorded here rather than faked:

| Plan asks for | Available here | What was done instead |
|---|---|---|
| `start.bat` + live backend on `:8000` | no uvicorn, no network | a static `python3 -m http.server` serving `web/` and `app/static/` |
| Chrome DevTools Performance panel | headless Chromium only, no DevTools UI | in-page `PerformanceObserver` / `performance.now()` probes, dumped through `document.title` |
| Chrome Task Manager CPU% | not available | not measured - claims that depend on it are marked *unverified* in the phase reports |
| 23 reference screenshots | headless screenshots possible, but every interesting state needs a live backend (chat stream, reminders, history, search) | **structural** references instead: computed-style fingerprints (below) |
| `pytest tests/ -q` (215 tests) | `pytest` not installed, no network to install it | `python3 -m compileall -q app tests scripts run.py config.py` |

**Consequence:** the visual safety net for P1/P2 is not a screenshot diff, it is a
**computed-style fingerprint** - for a given page, every element in `document.body` is walked, ~27
resolved CSS properties per element are concatenated and hashed to one number. Two builds that
render identically produce the same number. This is stronger than an eyeball diff for the specific
risk P1 carries (a deleted rule that was not actually a duplicate), and weaker for anything that
only appears in a state the static server cannot reach. Both facts are repeated in the phase reports.

---

## 1. Baseline sizes

Measured on the merged tree at the baseline commit, before any M14 edit.

| File | Lines | Bytes |
|---|---:|---:|
| `web/index.html` | 478 | 35,086 |
| `web/style.css` | 4,470 | 110,991 |
| `web/script.js` | 3,583 | 153,149 |
| `web/orb.js` | 459 | 24,255 |
| **JS total** (`script.js` + `orb.js`) | 4,042 | **177,404** |
| `web/api-monitor.html` | 60 | 2,369 |
| `web/api-monitor.js` | - | 5,940 |
| `web/viewer.html` | 525 | 20,495 |

CSS: **779** `{` occurrences in `style.css`; **733** rule blocks once `@media` / `@keyframes`
wrappers are discounted.

> Plan drift: section 0 of the plan states `index.html` is 474 lines. The delivered file is **478**.
> Every other baseline number in the plan matches exactly.

## 2. Budgets this milestone is measured against (plan 2.3)

| Budget | Target | Baseline |
|---|---|---|
| CSS delivered to `index.html` | < 80 KB | 110,991 B (single file) |
| JS total growth | at most +10% of 177,404 B | - |
| Idle CPU, tab focused | < 40% of baseline | *unverified - no Task Manager* |
| Idle CPU, tab hidden | approx 0% | *unverified* |
| Streaming frame p95 | < 16.7 ms | *unverified - needs a live stream* |
| Long tasks per turn | 0 | *unverified* |

## 3. Structural fingerprints (the actual regression gate)

Recorded per surface with the fingerprint probe described above. A phase that is supposed to be
purely non-visual must not move these numbers; a phase that is supposed to change rendering must
move them only where predicted.

| Surface | When | Body elements | Fingerprint |
|---|---|---:|---|
| `/jarvis/index.html` | baseline | recorded in P1 | recorded in P1 |
| `/dashboard` (Control Center) | baseline | recorded in P11 | recorded in P11 |
| `/watcher` | baseline | recorded in P11 | recorded in P11 |

## 4. Static checks run after every phase

```bash
node --check web/script.js
node --check web/orb.js
node --check web/api-monitor.js
node --experimental-detect-module --check web/js/<file>.js   # per ES module, from P0 onward
python3 -m compileall -q app tests scripts run.py config.py
```

`node --check` is necessary but **not sufficient** - it exits 0 on files the browser rejects (an
orphaned `else` left by a bad splice passes it). Every phase that edits JS therefore also loads the
page in headless Chromium and asserts that no error reached `window.onerror`.

## 5. Dev perf overlay (P0.4)

`web/js/devtools.js`, loaded from `index.html` as a module. Inert unless `?perf=1` or
`localStorage.jarvis_perf === '1'`. Shows fps, worst frame, long-task count, JS heap and DPR,
updated once per second. It adds **no** rAF loop when disabled - asserted by the P0 probe.

## P2 - motion, GPU and idle cost

Measured with the frozen computed-style fingerprint (animations and transitions
forced off, 31 properties x 375 elements = 11,625 rows), not with DevTools:
DevTools CPU profiling and the Task Manager are not reachable in this
environment, see the note above.

- Pre-P2 frozen fingerprint:  FP 2916808340, bodyElements 375
- Post-P2 frozen fingerprint: FP 428217816,  bodyElements 375
- Rows changed: 26 of 11,625, every one of them intended:
  - backdrop-filter x11: header 48px -> 20px, seven glass panels 32px -> 24px
    (saturate 1.2 -> 1.15), history dialog backdrop 4px -> 3px, cam panel
    blur(28px) -> none (its background moved 0.88 -> 0.92 to compensate).
  - contain x5: activity / history / search-results lists and the jarvis panel
    bodies gained layout paint. The chat message list is deliberately excluded.
  - color / border / background-color x10: native form controls picked up the
    dark UA palette from color-scheme dark. This is the intended effect.
- Zero layout properties moved and the element count is unchanged, so nothing
  reflowed.
- Full-app headless load after the JS half: no window.onerror and no unhandled
  rejection (probe reported an empty error list).

Removed or gated animation work:
- Two infinite star-field drifts and the logo sheen deleted outright; the
  duplicate gradientShift declaration on the welcome title removed.
- 26 transition:all declarations expanded to explicit property lists, so hovers
  no longer animate every computed property.
- The orb now stops entirely when the tab is hidden, when it scrolls out of
  view, when an opaque panel covers it, and when the user prefers reduced
  motion; its backing store is capped at DPR 1.25 and scales itself down when
  frames get expensive.

## P3 - Streaming resilience

What was wrong: a stream had exactly one safety net, a 300 second abort. A
socket that stayed open but silent held the UI hostage for five minutes, and
any failure replaced the half-written answer with a generic apology, so
whatever had already been generated was lost. There was no way to stop a
running answer.

What changed:

- An idle watchdog (45 s) rearms on every received byte, so a silent socket is
  caught in seconds rather than minutes. The overall cap moved to 600 s and is
  now a named constant.
- A Stop button shares the send slot; exactly one of Send/Stop is visible at any
  time. Escape does the same thing, except while a dialog is open, where Escape
  keeps its normal close-the-dialog meaning.
- Failures are classified rather than flattened: user Stop, PTT interrupt,
  death after some text arrived, and death before anything arrived. The first
  three keep everything already streamed on screen and mark it as incomplete.
- Recovery is offered inline: Regenerate when a partial answer exists, Retry
  when nothing arrived. Both resend the original user text.
- The finally block always clears both timers, removes the cursor, and releases
  the composer, so the UI can no longer be stranded mid-stream.

Verified: a headless harness with an abort-aware fake SSE body exercised the
happy path, Stop, Escape, Escape-with-dialog-open, mid-answer death,
no-byte death, and Retry resend. Partial text survived every interruption and
the composer was released on every path.

## P4 - Untrusted data in the DOM

What was wrong: reminder, note, todo and toast cards were built by concatenating
HTML strings. Most fields went through escHtml, but the reminder recurrence
field did not - that was a working stored-XSS path, since reminders can be
created by the assistant from arbitrary text. A todo list id was also spliced
straight into a CSS selector, and action image URLs were assigned to img.src
with no scheme check.

What changed:

- New web/js/dom.js with el()/icon()/replaceChildren(). script.js is still a
  classic script so it carries an identical shim, marked for deletion in P9.
- buildReminderCard, renderReminders, renderNotes, renderTodos and
  showReminderToast build real nodes and set textContent. No render path
  assigns markup any more; the remaining innerHTML uses write the empty string.
- List ids go through CSS.escape before being used in a selector.
- Action image URLs must match ^https?:// and carry referrerPolicy=no-referrer.
- The description div lost its inline style in favour of .reminder-card-desc,
  which carries exactly the same three declarations.

Verified: a probe rendered every surface with an <img src=x onerror=...>
payload in every field. The payload never executed, appeared as literal text,
and all button bindings (done/snooze/delete, note delete, note expand, todo
toggle/delete/add) still worked, including with a list id crafted to break out
of a CSS attribute selector.

Deferred: P4.6 (Content-Security-Policy). A CSP has to be sent as a response
header by the FastAPI app, not added from the client, and it needs every inline
handler and inline style audited first. Recorded here rather than half-applied.

## P5 - One send path

What was wrong: sendMessageWithImage was a stale copy of sendMessage. It had
its own 300 s abort with no idle watchdog, no Stop support, no cursor cleanup,
and on failure it appended a second assistant bubble underneath the partial
one.

What changed: the duplicate is gone (96 lines). sendMessage takes an optional
imageOverride; when present the camera is not touched again and the frame is
simply attached. Text-only, live-vision and pre-captured sends now share one
lifecycle, one abort controller, and every P3 guarantee. The composer is only
cleared for genuinely typed messages, so a programmatic send no longer wipes
whatever the user was typing.

Verified: the image path sends the frame and the vision bypass token, produces
a single assistant bubble, leaves the composer untouched, and - new behaviour -
responds to Stop while keeping its partial answer.

Note on harness fidelity: the fake SSE reader renders only the first chunk of a
multi-chunk answer. The identical harness run against the pre-P3 baseline
behaves exactly the same way, so this is an artifact of the stub, not a
regression introduced by these phases.

## P6 - Streaming-safe markdown

What was wrong: assistant replies were written straight into textContent, so
every reply showed its raw syntax - **bold**, ### headings, - bullets, and code
fences as literal backtick lines. Both models emit markdown by default, so this
hit nearly every substantial answer. The notes panel had the same problem while
storing a field literally called markdown_body.

What changed: a hand-written renderer in web/js/markdown.js, no library.

- Supported subset: ATX headings, fenced and indented code, bullet and numbered
  lists (two levels), blockquotes, thematic breaks, tables with alignment,
  strong, em, del, inline code, links, and bare autolinks. Reference links,
  footnotes, raw HTML, real images, task boxes and math are deliberately out.
- Streaming repair runs on every frame: a dangling fence is closed with its own
  marker and run length, and half-written emphasis or links are dropped rather
  than rendered half-applied. Nothing flashes on and off mid-stream.
- Rendering diffs at block level. Only the last block can still change, so
  earlier DOM nodes are reused instead of being rebuilt on every chunk, and one
  render is coalesced per animation frame.
- The final pass runs with repair disabled, so the last thing on screen is
  exactly what the model said.
- Zero innerHTML. Text always lands in textContent, so raw HTML in a reply is
  visible literal text and injection is structurally impossible. Link schemes
  are limited to http, https and mailto; anything else renders as text.
- Fenced blocks get a language label and a Copy button, and that button only
  appears once the fence can no longer change, so it can never copy half a block.
- History transcripts and expanded notes render through the same code path.
  User messages stay plain text on purpose, and note previews stay plain because
  markdown of a 120-character fragment is meaningless.
- If the module fails to load, the call site falls back to plain text rather
  than showing an empty reply, and a renderer throw falls back the same way.

Traps that actually bit, recorded so they are not re-introduced:

- Counting fence markers is wrong; the closer must match the opening marker's
  character and run length, or inline code spans and longer fences break it.
- Intraword underscores must not emphasise, or session_id and snake_case_name
  turn italic. Backslashes before non-punctuation must stay literal, or Windows
  paths lose their separators.
- A flex item defaults to min-width auto, so one long code line would widen the
  bubble and then the whole chat area. min-width: 0 on .msg-content is what
  makes the horizontal scroll on <pre> actually work.

Verified: 38 unit tests in web/js/markdown.test.mjs (run with node, they install
a small DOM shim), plus two headless browser probes covering a streamed torture
reply, no mid-stream flashing, Windows paths and identifiers, copy behaviour,
wide code not widening the bubble, the injection cases, the plain-text fallback,
reopened history, and note expansion.

Harness note for future phases: a fake fetch must route by URL. The app polls
notes, reminders and history in the background, and a stub that answers every
request from one fake SSE reader silently eats stream frames - which looks
exactly like the app dropping chunks, and is not.

## P8 - Stacking scale, dead CSS, and the stylesheet split

### P8.1 z-index scale

Every raw z-index in the stylesheet is now a token declared once in
`css/tokens.css`:

    --z-orb: 0        --z-content: 100   --z-chrome: 200   --z-fab: 300
    --z-panel-overlay: 390   --z-panel: 400   --z-float: 500  --z-toast: 600
    --z-modal-back: 700      --z-modal: 710   --z-dev: 99999

Five raw values survive on purpose because they stack inside a parent that
already owns a layer, and promoting them to the global scale would be a lie:
`.mode-btn` (1), `.history-item-menu` (3), `.history-item.menu-open` (5),
and the two decorative `-1` layers on `body::after` and `#orb-container::before`.

`.history-dialog` had no z-index at all. It rendered above the reminder toasts
only because it happened to appear later in the document; a delete confirmation
losing to a toast is a real misclick risk, so it is now `--z-modal`.

Verified in the browser: panel 400 < float 500 < toast 600 < dialog 710, and
the overlay sits at 390, below the panel it dims.

### P8.2 !important audit

Seven of the seventeen `!important` declarations were deleted. Five on
`.send-btn` and two on `.cam-panel.minimized` were all defending a
two-class selector against a one-class one, which specificity already wins.
After the removal the send button still computes 38px/38px/10px and the
minimized camera still computes exactly 72px by 72px.

Ten survive, and they are the legitimate kind: `.history-overlay[hidden]`, and
the P2.1 reduced-motion block, which exists precisely to overrule whatever the
rest of the stylesheet asked for.

TRAP: `.cam-panel` animates width and height over 0.25s. Measuring the panel
immediately after toggling `.minimized` reads a frame of the transition, not
the rule - the first probe run reported 378x284 and looked like a regression.
Freeze transitions before measuring size in any probe.

### P8.3 Click-to-front

`.cam-panel` and `.jarvis-panel` share `--z-float`, so DOM order silently
decided which one the user could read. `initFloatPanelStacking()` promotes the
panel under the pointer to `calc(var(--z-float) + 1)` on the capture phase of
mousedown and clears the inline value on the others. Capture phase matters:
the drag handlers on these panels stop propagation.

### P8.4 / P8.5 The split - and a deviation from the plan

`web/style.css` became six files under `web/css/`: tokens, base, layout, chat,
panels, floats. `index.html` links all six directly rather than using @import,
because @import is serial - the browser cannot discover the second file until
the first is fetched and parsed. `web/style.css` survived as an @import shim
until P11.4 repointed its last consumer (api-monitor.html); it was deleted in
P12. Anything still asking for it should link the six files directly, in order.

DEVIATION: the plan called for a seventh file, `responsive.css`, holding every
media query and loaded last. The splitter's own check rejected that. This
stylesheet declares 32 base rules AFTER a media block, and at equal specificity
a media query wins only if it comes later, so hoisting all media queries to the
end would have flipped 32 rules at narrow widths. Each media block therefore
stays with the section it belongs to, and `responsive.css` is not written at
all because nothing global was left for it.

The split is a pure move, and it is proved rather than asserted:

1. Every one of the 573 top-level chunks lands in exactly one file, verified by
   a character-multiset comparison against the original.
2. Rules that share a selector are unioned into one group and move together -
   13 chunks were regrouped this way, because several rules comma-group a side
   panel with a floating one.
3. Source order is proved for all 552 distinct selectors, including selectors
   nested inside media queries: if rule A precedes rule B in the original and
   both touch one selector, A's file must not load after B's. Ten chunks were
   pushed into a later file to satisfy this.
4. A computed-style fingerprint over all 380 body elements and 31 properties
   each, with animation frozen, is byte-identical before and after:
   FP 1653324858, 11780 rows.

TRAP: the first fingerprint comparison reported a difference that did not
exist. The harness walked every element in the document, and the split page has
five more `<link>` elements in `<head>` than the single-stylesheet page did.
Fingerprint the body only.

### P8.6 Dead CSS

Ten `.speech-widget` rules were deleted. The widget is absent from every .js,
.html and .py file in the tree, so those rules could never match. The
fingerprint is unchanged by the deletion, which is what dead means.

## P9 - Module split

`web/script.js` (3,583 lines) and `web/orb.js` are gone. The app is now 25 ES
modules under `web/js/`, loaded through a single entry:

    <script type="module" src="./js/main.js?v=20260804-p9"></script>

Four rules hold this together. All four were learned by breaking them.

1. **Nothing imports the entry module.** `index.html` loads `main.js?v=...`
   while `chat.js` imported `./main.js`; the query string made those two
   different specifiers, so the browser instantiated `main.js` twice and the
   second copy read the first copy's `const` bindings before they existed
   ("Cannot access 'stopBtn' before initialization"). The entry imports
   everything; nothing imports the entry. The import generator asserts this.

2. **The shared-state module imports nothing.** `state.js` has no imports at
   all. `config.js` seeds it with `Object.assign(state.settings,
   DEFAULT_SETTINGS)` rather than `state.js` reaching for the defaults.

3. **Constants must not depend on behaviour.** `config.js` may import only
   `state.js`. It once pulled in `orbctl.js` through `loadSettings()`, which
   made a constants file depend on a UI controller and produced "Cannot access
   'ORB_DEFAULT_GLOWS' before initialization". `loadSettings`/`saveSettings`
   now live in `panels.js`.

4. **Never give a module-local helper the same name as another module's
   export.** The import generator works from a global owner map and cannot see
   module-local declarations, so a local `function render()` in `commands.js`
   collided with `markdown.js`'s `render` and produced "Identifier 'render' has
   already been declared". The local is now `renderResults`.

`js/bus.js` carries six frozen events - `turn:start`, `turn:end`,
`activity:row`, `verdict:fail`, `session:change`, `orb:state` - and warns on an
unknown name rather than failing silently.

The orb exposes a real state API (`getStateConfig`, `setStateConfig`,
`replaceStateConfigs`, `resetStateConfigs`, `snapshotStateConfigs`,
`stateNames`, `onOrbStateChange`). The dashboard's monkey-patch is deleted.

## P10 - Chrome, commands, icons, geometry, polish

**P10.1 Header overflow menu.** Three controls moved behind a `...` button with
the standard menu-button keyboard pattern. Verified at 1400px and 480px: menu
in viewport, right aligned, Escape restores focus to the trigger. The header
must not gain `overflow: hidden` or the menu is clipped.

**P10.2 Command palette** (`js/commands.js`). `Ctrl`/`Cmd`+`K`, and it rejects
`Shift` and `Alt` because PTT owns `Ctrl+Shift`. Subsequence scoring, combobox
and listbox roles, `aria-activedescendant` on the input so focus never leaves
it. Twelve commands registered; two are hidden by their `when()` at rest.

**P10.3 Shortcuts overlay** (`js/shortcuts.js`). `?` or `F1`, built from the
same registry so it can never drift from reality. `?` is ignored while typing.
The settings panel points at both `?` and `Ctrl+K`.

**P10.4 Icon system** (`js/icons.js`). Fourteen inline SVGs, one frozen path
table, `aria-hidden` unless a label is passed. Zero emoji remain in
`web/js/*.js` as UI content, and every icon-only button has a name.

**P10.5 Panel geometry** (`js/geometry.js`). Positions persist to
`localStorage['jarvisPanels']` on drag end - never during a drag, which would
be a synchronous main-thread write per frame.

  *Geometry is a hint; the viewport wins.* Every restore goes through
  `clampBox()`, and a debounced `resize` handler re-clamps, so a panel saved on
  a wide monitor can never open off-screen and unreachable. At least 48px stays
  grabbable. A corrupt or array payload parses to `{}` and warns.

  This is not a repeat of the M12 mistake (CLAUDE.md S19), where localStorage
  was removed as the source of truth for the OPEN CONVERSATION because two tabs
  shared one key. Panel position is a per-user preference, not per-tab state,
  and two tabs agreeing on it is desirable.

**P10.6 Toasts and auto-close.** At most three toasts are visible; the rest
queue behind a "+N more" line and none are dropped. The dismiss timer pauses on
hover and on focus, because a message that vanishes while being read is a bug.

  The 30-second auto-close on the reminders and notes panels is deleted. A
  panel the USER opened now stays open. Only a panel opened by the AGENT (via
  `handleActions`, which passes `{auto: true}`) closes itself, after 60s, and
  `armAutoClose` cancels on the first pointerdown, keydown, focus or wheel -
  bound in the capture phase, because the panel's own handlers stop
  propagation and a missed cancel would close the panel under the user's hand.

**P10.7 Polish.** Copy button on assistant replies, holding the raw markdown as
a JS PROPERTY (`_rawMarkdown`) and never a `data-` attribute: replies run to
thousands of characters and an attribute is serialised into the DOM every time.
`aria-busy` on the thread while streaming, set in `finally` so a crashed turn
cannot strand it. The to-do checkbox is a real `<button role="checkbox"
aria-checked>` instead of a `<div>` with a click handler. Focus rings added for
ten previously invisible controls.

  *Scroll anchoring:* `scrollToBottom()` no longer fires unconditionally. It
  moves the view only when the user is already within 80px of the bottom;
  `scrollToBottom(true)` is reserved for the user's own actions - sending a
  message, opening a conversation, pressing the scroll button.

### Headless verification notes

Two whole rounds were lost to measurement artifacts, so they are recorded here:

- `requestAnimationFrame` NEVER fires under `--virtual-time-budget`, exactly
  like `requestIdleCallback` in P7. Anything deferred by a frame appears not to
  happen. Prove the guard, not the frame.
- `.chat-messages` sets `scroll-behavior: smooth`, so a `scrollTop` write
  starts an animation headless never finishes and every read comes back 0.
  Set `scrollBehavior = 'auto'` in the probe first.
- A `<script type="module">` that fails to resolve is skipped in SILENCE. Use a
  classic `<script>` with `await import()` inside a try/catch, and never put a
  query string on that import or you get a second module instance.

## P11 - One visual system across five surfaces

### What the plan assumed, and what was actually true

The plan described the two admin dashboards as near-duplicates and asked for
the shared CSS to be lifted into `app/static/admin.css`. A first extraction
script refused to finish: its own sanity gate fired with "suspiciously little
shared CSS". The gate was right and the plan was wrong, so the premise was
measured before the gate was touched (`/data/p11_diag.py`):

| Measurement | Control Center | Watcher |
|---|---:|---:|
| CSS chunks | 59 | 69 |
| Selectors present in both | 18 | 18 |
| ... with identical declarations | 6 | 6 |
| ... with differing declarations | 12 | 12 |
| Selectors unique to this file | 41 | 51 |
| Bytes that could be shared verbatim | 342 | 342 |

Six rules are genuinely identical: `*`, `html,body`, `a`, `.brand`, `h1`,
`h1 small`. Twelve more share a selector but not a body - `.card`, `.pill`,
`.bar`, `.dot`, `body`, `footer`, `header` and friends are styled differently
on each page on purpose. Merging those would have silently restyled both.

So the extraction has two rules, not one:

- **Rule A** - identical declarations move to `admin.css` verbatim.
- **Rule B** - applies to `:root` only. One page's token block was a subset of
  the other's, so the superset moves and both pages read from it. This is safe
  for `:root` specifically because an unused custom property is inert; the same
  merge on `.card` would change rendering. The script asserts every declaration
  it merges starts with `--`.

A per-page gate then re-checks that every declaration the page had before is
still delivered after the move.

**Proof it works, not just that a `<link>` exists:** `--accent` in `admin.css`
was changed to `#ff00ff` and both dashboards were re-measured headlessly. Both
reported `#ff00ff`; both returned to `#3da9fc` when it was reverted. (The first
attempt appeared to fail because Chromium served a cached stylesheet from its
profile - re-run with a fresh profile and `--disk-cache-size=1`.)

### Deviation: `app/static/` is not mounted

`admin.css` lives next to the dashboards it serves, but `app/main.py` only
mounts `web/` (at `/jarvis` and `/app`). Nothing served `app/static/`. The plan
said to stop and choose rather than guess, so: **one additive route was added
to the existing `app/api/dashboard.py`** -
`GET /static/admin.css` returning a `FileResponse`. A `StaticFiles` mount was
deliberately not used - the directory holds two HTML files that are already
served by their own routes, and mounting it would expose them at a second URL
with different behaviour.

### P11.3 viewer.html - repaired, not deleted

The plan allowed deletion if nothing reached the page. Nothing links to it
(checked in `web/`, in every API route, and in every agent tool), but it sits
inside the `/jarvis` mount, so `/jarvis/viewer.html` has always served and a
bookmark is invisible to grep. It was repaired instead:

- The dead half is gone: it polled `GET /tasks/{task_id}` once a second, up to
  120 times, against a route removed with the background-task system. Every
  visit was two minutes of 404s behind a spinner, ending in "Task timed out.
  Please try again" - which was not true and could not be fixed by retrying.
- It now takes content from query parameters (`?image=<https url>` or
  `?text=<text>`) or from a same-origin `postMessage`, and says plainly when it
  has nothing to show.
- The image URL is guarded with the same `^https?://` test the chat uses, so a
  `javascript:` URL cannot reach `src`.
- Tokens now match the chat (it already used the chat accent; `--bg`, the text
  ramp and the font had never followed). They are a documented COPY, not a
  shared file: this page must render standalone, with the app possibly down.

### P11.6 Cross-surface navigation

The four admin-ish surfaces get a visible `.surface-nav` row with
`aria-current="page"` on the current one. **The chat deliberately does not.**
It is the surface a person keeps open all day, and four permanent header links
used once a week is how a header rots - so it reaches the others through four
commands in the palette (Ctrl+K, group "Links"), each opening a new tab so a
reply in flight is never abandoned.

### Verification

| Surface | Result |
|---|---|
| Chat | 16 commands registered, 14 available, `Links` group = 4, boots clean |
| Viewer | bg `rgb(5,5,16)`, Poppins, empty state shown, spinner hidden, **0 fetches** |
| API Monitor | 6 parallel stylesheets, all versioned, 0 `@import` rules, shim unlinked |
| Control Center | `admin.css` parsed, `--accent` `#3da9fc`, nav 5 links, 1 marked current |
| Watcher | identical token readout - same sheet, proven by the sentinel swap |

## P12 - Accessibility, caching, and the last stale references

### P12.1 Contrast: measured, and it passes

Ratios were computed rather than eyeballed (`/data/p12_contrast.py`), with
alpha composited properly - most of this text sits on glass, not on the page
background.

| Foreground | On page `#050510` | On glass | AA needs |
|---|---:|---:|---:|
| `--text` | 17.41:1 | 17.01:1 | 4.5 |
| `--text-dim` | 5.31:1 | 5.31:1 | 4.5 |
| `--accent` | 4.99:1 | 4.86:1 | 4.5 |
| `--accent-secondary` | 10.48:1 | 10.22:1 | 4.5 |
| `--text-muted` | 2.35:1 | 2.39:1 | 4.5 |

All thirteen selectors the plan flagged as suspects **pass**: they use
`--text-dim`, which clears AA on both backdrops. `--text-dim` was therefore NOT
raised - changing a passing token to fix a problem that does not exist is how
you break a design system. The break-even point is white at alpha 0.452; the
token sits at 0.50.

`--text-muted` is the one below AA. It is used for placeholders, timestamps and
disabled hints - decorative or duplicated elsewhere - so it stays, and
`prefers-contrast: more` lifts it to 0.65 for anyone who asks for more.

**Honest limits:** text over the animated star field and over `backdrop-filter`
glass has no constant background, so no static tool can measure it. Those need
a screenshot sample and were not checked here.

### P12.2 The thread was not keyboard-scrollable

`body { overflow: hidden }` was checked and kept - this is a fixed-height
`100dvh` layout where the THREAD scrolls, not the page. The real defect was
that a `overflow-y: auto` div can only be scrolled by keyboard if it can hold
focus, and it had no `tabindex`. It now has `tabindex="0"`, `role="log"`,
`aria-live="polite"` and `aria-relevant="additions"`, and a `:focus-visible`
ring that does not fire on mouse clicks.

`role="log"` plus the existing `aria-busy` toggle around streaming (P10.7) is
what keeps a screen reader from re-reading the whole message on every chunk:
additions are announced, and the subtree is marked busy while it changes.

### P12.3 Closed panels were a focus trap in reverse

Measured before fixing: with every panel closed, **36 focusable controls** sat
inside them and the browser accepted focus on them. The panels hide with a
transform or with `visibility`, both of which leave the element in the tab
order. A keyboard user tabbing out of the header disappeared into an invisible
panel.

The first implementation decided visibility from geometry and was thrown away
after measurement, for two independent reasons:

1. Under Chromium's virtual-time budget CSS transitions never advance - an
   opened panel still reported `transform: matrix(1,0,0,1,-340,0)` 1400ms
   later. Geometry-based logic can never be verified in this harness.
2. **It would have shipped a real bug.** Panels slide for ~250ms, during which
   geometry says "off-screen" - so the panel would be `inert` while opening,
   and `openHistoryPanel()` focuses its search box immediately. Focus into an
   inert subtree is refused, so opening history by keyboard would have quietly
   stopped focusing the search box.

Openness now comes from the state the app already keeps. That was measured too:
five panels maintain `aria-hidden` (history, notes, reminders, orb dashboard,
camera) and three drive off a class alone (activity, search results, settings).
Either signal means open, which also repaired the second bug - those three
never told a screen reader they were closed, and one sweep now drives both
`inert` and `aria-hidden` so they cannot drift apart again.

Also in P12.3: a skip link that is the first focusable element in the document
and genuinely becomes visible on focus (measured: top -44px blurred, 0px
focused), and a `prefers-contrast: more` block that drops `backdrop-filter`,
makes the glass opaque and fades the orb to 0.18.

### P12.4 One version string, and headers that make it mean something

All six stylesheets and the JS module now carry the same `?v=20260804-p12`.
`app/core/middleware.py` was modified (not duplicated): `?v=` assets get
`public, max-age=31536000, immutable`; everything else gets
`no-cache, must-revalidate`. HTML is never immutable - it carries the version
strings, and caching it for a year is how you ship an app that cannot update
itself.

There is no FastAPI in this sandbox, so the policy was verified by extracting
the real decision expressions from the source file and evaluating them against
a 16-case truth table - testing the code that will run, not a retyped copy of
it. 16/16 correct.

### P12.5 Description, favicon, and a notification icon that exists

A `<meta name="description">`, an inline-SVG favicon (`web/favicon.svg` - the
orb reduced to two rings and a core; no text, because a letter at 16px is a
smudge), and a fix to `notifications.js`, which asked for `/web/favicon.ico`:
the app is mounted at `/jarvis`, not `/web`, and no `.ico` ever existed, so
every native reminder notification had been showing the browser default. The
tab title now reads `<conversation> - J.A.R.V.I.S`, conversation first because
tab strips truncate from the right.

### Cleanup finished

`web/style.css` (the @import shim) was deleted after all ten remaining
references were checked by hand - nine prose comments and one file list in
`scripts/_m13_doc_audit.py`, which still expected `web/script.js` too. Both
that list and a comment in `chat_service.py` naming `script.js` were repaired;
the comment now names the modules that actually read `decision.query_type`,
found by scanning rather than assumed.

### Verification

| Check | Result |
|---|---|
| Skip link is first focusable | yes, reveals on focus (-44px -> 0px) |
| Closed panels inert / aria-hidden | 8 of 8, **0 focus leaks** |
| Opening lifts inert within 50ms | yes, focus into the panel works |
| Closing restores it | yes |
| Thread focusable, `role="log"` | yes |
| Tab title | `Weekend trip planning - J.A.R.V.I.S`, falls back cleanly |
| Assets on one version string | 7 assets, 1 distinct version |
| Favicon fetch | 200 |
| Cache policy truth table | 16/16 |
| markdown tests | 41/41 |
| `compileall` / module syntax sweep | clean |
| Boot | `bodyEls` 389 static (+1 skip link), composer enabled |
