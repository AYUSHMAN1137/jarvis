# M14 UI milestone - phase report

Phases P0 through P10 are documented in `docs/UI_BASELINE.md`, which was
written as the running record for this milestone. The plan asks for this
file, so P11 and P12 use its template here and the earlier phases are
cross-referenced rather than duplicated.

---

## P11 - Cross-surface consistency
**Commit:** uncommitted (commits paused at user request after P6)
**Files changed:** `app/static/admin.css` (new, 4715 B), `app/static/dashboard.html`,
`app/static/watcher_dashboard.html`, `app/api/dashboard.py`, `web/api-monitor.html`,
`web/viewer.html`, `web/css/base.css`, `web/js/main.js`

### What changed
The shared parts of the two admin themes moved into one stylesheet, four
surfaces gained a navigation row, the API monitor stopped loading CSS through
an `@import` shim, and `viewer.html` stopped polling a route that was deleted
with the background-task system. The chat reaches the other surfaces through
the command palette instead of permanent header links.

### Measurements
| Metric | Before | After | Target | Met? |
|---|---|---|---|---|
| Admin CSS shared between dashboards | 0 B | 342 B in `admin.css` | "the shared block" | yes, but see deviation |
| Token blocks defining admin colours | 2 | 1 (superset) | 1 | yes |
| API monitor stylesheet requests | 1 serial + 6 chained | 6 parallel | parallel | yes |
| Viewer requests to dead routes | up to 120 per visit | 0 | 0 | yes |
| Surfaces reachable from any other | 0 | 5 | 5 | yes |

### Verification
- Sentinel proof: one `--accent` change in `admin.css` moved BOTH dashboards.
- Headless probes on chat, viewer, API monitor, and both dashboards: all green.
- `compileall` clean; markdown tests 41/41; module syntax sweep clean.
- pytest could not be run: **not installed in this sandbox, and there is no
  network to install it.** The plan's 215 tests remain unrun. Stated, not
  glossed over.

### Deviations from the plan
1. **The plan overstated the duplication between the dashboards.** Measured:
   18 shared selectors, only 6 with identical bodies, 342 shareable bytes. The
   extraction was rewritten around the measurement instead of forcing a larger
   merge that would have restyled both pages.
2. **`app/static/` is not mounted**, so a stylesheet placed there would have
   404ed. Added one route (`GET /static/admin.css`) to the existing dashboard
   router rather than a `StaticFiles` mount, which would have exposed both
   dashboard HTML files at a second URL.
3. **`viewer.html` was repaired, not deleted.** Nothing links to it, but it is
   inside the `/jarvis` mount and a bookmark is invisible to grep.

### Traps hit
- A sanity gate fired because the plan's premise was wrong. Measuring the
  premise beat weakening the gate.
- A diagnostic script nearly imported an edit script for its helpers, which
  would have run the whole extraction as a side effect. Diagnostics must be
  standalone.
- Chromium served a cached stylesheet from its profile and made a passing
  sentinel test look like a failure. Fresh profile + `--disk-cache-size=1`.

### Left undone
The visible nav row is not on the chat surface, by design. Admin tokens are a
second palette by design - two dialects (ambient chat, dense admin), not three.

---

## P12 - Accessibility, caching, metadata
**Commit:** uncommitted (commits paused at user request after P6)
**Files changed:** `web/index.html`, `web/css/base.css`, `web/js/panels.js`,
`web/js/config.js`, `web/js/main.js`, `web/js/history.js`,
`web/js/notifications.js`, `web/favicon.svg` (new), `app/core/middleware.py`,
`app/services/chat_service.py`, `scripts/_m13_doc_audit.py`,
`web/style.css` (deleted)

### What changed
Contrast was measured and found already compliant, so nothing was recoloured.
The conversation thread became keyboard-scrollable, closed panels stopped
holding 36 invisible tab stops, a skip link was added, versioned assets became
cacheable for a year while HTML keeps revalidating, and the last references to
two deleted files were repaired.

### Measurements
| Metric | Before | After | Target | Met? |
|---|---|---|---|---|
| Suspect selectors below AA | 0 of 13 (measured) | 0 of 13 | 0 | yes |
| Focusable controls inside closed panels | 36 | 0 | 0 | yes |
| Panels announcing themselves while closed | 3 | 0 | 0 | yes |
| Tab stops before the composer | 13+ | 1 (skip link) | few | yes |
| Distinct asset version strings | 3 | 1 | 1 | yes |
| Cache policy cases correct | n/a | 16/16 | all | yes |
| Notification icon requests that 404 | every one | 0 | 0 | yes |

### Verification
- Headless probe: skip link first and revealed on focus; 8/8 panels inert with
  zero focus leaks; inert lifts within 50ms of opening and returns on close;
  thread focusable with `role="log"`; title updates; 7 assets on one version.
- Cache policy: real expressions extracted from `middleware.py`, 16-case truth
  table, 16/16.
- Contrast: computed with proper alpha compositing, both backdrops.
- Static checks: module syntax sweep clean, `compileall` clean, markdown 41/41.
- pytest: **not installed, no network. Not run.**

### Deviations from the plan
1. **`--text-dim` was not raised.** The plan expected failures; measurement
   found 5.31:1 against a 4.5:1 requirement. Changing a passing token to fix an
   imagined problem is how a design system rots.
2. **`body { overflow: hidden }` was kept.** The survey called it a
   keyboard-scroll bug; it is load-bearing for a `100dvh` layout. The actual
   defect was the missing `tabindex` on the scroll container.
3. The inert sweep is state-driven, not geometry-driven - the geometric version
   would have broken focus-on-open during the slide animation.

### Traps hit
- **CSS transitions do not advance under Chromium's virtual-time budget.** Same
  family as `requestAnimationFrame` never firing. Any probe measuring a
  transition-driven end state must set `transition: none` first.
- A verification gate grepped the source for `/tasks/` and failed on the
  comment explaining why the polling was removed. Gate on behaviour, not prose.

### Left undone
- Text over the star field and over `backdrop-filter` glass cannot be contrast
  checked statically; it needs a screenshot sample.
- The 215-test suite has never run in this environment.
