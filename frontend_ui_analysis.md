# JARVIS — Frontend UI Analysis

> Scope: **web/** (chat UI, dashboards) + **app/static/** (admin dashboards). M13 backend plan touch nahi kiya.

---

## 1. UI Surfaces (4 alag apps)

| Surface | Path | Purpose | Stack |
|---|---|---|---|
| **Main chat** | [web/index.html](file:///c:/Users/ayush_lr8ru2y/Desktop/jarvis/web/index.html) | Primary JARVIS chat — orb, streaming, voice, vision | Vanilla HTML/CSS/JS, WebGL orb |
| **Control Center** | [app/static/dashboard.html](file:///c:/Users/ayush_lr8ru2y/Desktop/jarvis/app/static/dashboard.html) | Health, system status, learner stats | Inline `<style>`, dark cyan/blue theme |
| **Watcher** | [app/static/watcher_dashboard.html](file:///c:/Users/ayush_lr8ru2y/Desktop/jarvis/app/static/watcher_dashboard.html) | OS-level watcher: mic/cam/brightness/wifi toggles | Inline `<style>` (same admin theme) |
| **API Monitor** | [web/api-monitor.html](file:///c:/Users/ayush_lr8ru2y/Desktop/jarvis/web/api-monitor.html) | Live API-key usage events + grid | Reuses `style.css` |
| **Viewer** | [web/viewer.html](file:///c:/Users/ayush_lr8ru2y/Desktop/jarvis/web/viewer.html) | Standalone content viewer | Fully self-contained inline CSS |

---

## 2. Main Chat UI — `web/` (the flagship)

### Structure (479-line [index.html](file:///c:/Users/ayush_lr8ru2y/Desktop/jarvis/web/index.html))
- Full-screen glassmorphic chat, single column
- Sticky header: logo, mode pill, status badge, 7 icon buttons (history / activity / search / orb-dash / settings / monitor / new-chat)
- Slide-in **panels**: history, activity, search results, settings, orb-customization
- Floating **draggable panels**: camera (vision), reminders, notes/todo
- Welcome screen with 6 suggestion chips
- Input bar with actions: reminders, notes, camera, mic (push-to-talk Ctrl+Shift), TTS, send
- WebGL ambient orb behind everything ([orb.js](file:///c:/Users/ayush_lr8ru2y/Desktop/jarvis/web/orb.js), 460 lines, 6 states: idle/listening/thinking/searching/working/speaking)

### Design tokens ([style.css](file:///c:/Users/ayush_lr8ru2y/Desktop/jarvis/web/style.css), 4471 lines)
```css
--bg: #050510;                    /* deep space */
--accent: #7c6aef;                /* violet */
--accent-secondary: #4ecdc4;      /* teal */
--glass-bg: rgba(10,10,28,0.72);
--glass-border: rgba(255,255,255,0.06);
--font: 'Poppins', ...;
```
- Animated star-field via `body::before/::after`
- `backdrop-filter: blur(32px) saturate(1.2)` glass panels
- `--danger`, `--success`, radius scale, safe-area insets

### JS ([script.js](file:///c:/Users/ayush_lr8ru2y/Desktop/jarvis/web/script.js), ~3k lines)
- SSE streaming chat: `fetch(\`${API}/chat/jarvis/stream\`)`
- EventSource for proactive notifications: `/api/notifications/stream`
- REST: reminders, notes/todos, transcribe, startup-brief, activity, TTS, health
- State machine syncs UI ↔ orb states

---

## 3. Admin Dashboards — `app/static/` (different design language)

| | Chat UI | Admin dashboards |
|---|---|---|
| Palette | Deep purple/space, Poppins | Navy/cyan `#070b12`, Segoe UI |
| Style source | External `style.css` | Inline `<style>` per page |
| Accent | `#7c6aef` | `#3da9fc` |
| Feel | Consumer / ambient | Ops / data-dense |

Both dashboards use the **same** 12-col card grid, pills, tables, gradient brand mark `JV`. They're consistent with each other — just intentionally separate from the chat app.

---

## 4. Strengths ✅

1. **Cohesive flagship**: chat app has one clear visual identity (space-purple glass + orb) — genuinely premium
2. **WebGL orb** with 6 semantic states tied to agent lifecycle — signature element
3. **Accessibility already in**: ARIA labels, `aria-hidden` on panels, `aria-live` toast regions, keyboard push-to-talk, focusable dialogs
4. **Mobile-ready**: `viewport-fit=cover`, safe-area insets, `100dvh`, `maximum-scale=1.0` to stop iOS zoom
5. **Panel system**: draggable, resizable, minimizable floating panels (cam/reminders/notes) — rare in a hobby chat UI
6. **Deep-link support** via `<base href="/jarvis/">` with comment explaining why
7. **Separation of concerns**: chat vs admin dashboards are visually distinct on purpose

---

## 5. Issues & Opportunities 🔧

### A. Maintainability
| Issue | Where | Fix |
|---|---|---|
| **4471-line monolith CSS** | [style.css](file:///c:/Users/ayush_lr8ru2y/Desktop/jarvis/web/style.css) | Split into `tokens.css`, `layout.css`, `chat.css`, `panels.css`, `orb.css` — or at least section banners with TOC |
| **~3k-line script.js** | [script.js](file:///c:/Users/ayush_lr8ru2y/Desktop/jarvis/web/script.js) | Split into modules: `api.js`, `chat.js`, `voice.js`, `vision.js`, `panels.js`, `state.js`, use `type="module"` |
| **Duplicated admin theme vars** | dashboard.html / watcher_dashboard.html inline | Extract a shared `admin.css` and link it from both |
| **Cache-bust versions by hand** (`?v=20260725-v2`) | index.html | Use a tiny build step or let server add content-hash |

### B. Consistency
- `viewer.html` uses **yet another** palette (`#0a0a1a` + Segoe UI) — third dialect. Either align with chat or admin theme.
- Buttons / pills / cards re-defined 3 times. A minimal shared tokens file (`tokens.css`) would unify.
- Some emojis used as icons (⏰ 📋 🎙️) alongside SVG icons — inconsistent affordance.

### C. UX gaps
- **No global keyboard shortcuts map** (users discover Ctrl+Shift by accident). Add `?` shortcut help overlay.
- **History panel** doesn't show active conversation highlight region info / unread indicator.
- **Toast container** has no max count / queue — bursts of proactive notifications could stack.
- Mode switch is single (Jarvis only). If more modes planned, pill will need redesign.
- No visible **error recovery** on chat stream failure (need to verify — can't see from static read).

### D. Performance
- `style.css` 106KB — fine loaded once, but `?v=` busting defeats cache on every manual bump. Use fingerprinting.
- Star-field uses two 200%-size fixed pseudo-elements with always-on `120s/160s` animations — costs GPU on low-end devices. Could respect `prefers-reduced-motion`.
- Fonts: Poppins loaded from Google without `display=swap` fallback guarantee is OK, but consider `preload` on the CSS.

### E. Accessibility quick wins
- `body { overflow: hidden }` breaks keyboard scroll on desktop edge cases
- Some color-mixed labels (status "Online") may fail contrast at `--text-dim` on `--bg`
- `.btn-icon` size appears ~32×32; WCAG target ≥ 24×24 OK but hover-only titles aren't readable on touch — already have `aria-label` though ✅

### F. Futures worth considering
- Theme switcher (dark-only right now) — at least a **dim mode** vs pure dark
- Panel **snap-to-edge** / layout memory (persist panel positions to localStorage)
- **Command palette** (`Ctrl+K`) — fits JARVIS identity perfectly
- Server-rendered loading skeleton for first paint (currently blank → orb fade-in)

---

## 6. Quick Priority Stack

| # | Effort | Impact | Action |
|---|---|---|---|
| 1 | Low | High | Extract admin shared CSS (one `.css` file for both dashboards) |
| 2 | Low | High | Add `prefers-reduced-motion` guard for star-field + orb lerps |
| 3 | Med | High | Split `script.js` into ES modules (api/voice/panels/chat) |
| 4 | Med | Med | Split `style.css` by component, introduce `tokens.css` |
| 5 | Low | Med | Align `viewer.html` to chat tokens |
| 6 | Med | Med | Keyboard shortcut overlay (`?`) |
| 7 | Low | Low | Cap toast queue at 3, FIFO |
| 8 | High | Med | Command palette `Ctrl+K` |

---

## 7. What I did NOT touch

- `IMPLEMENTATION_PLAN_M13.md` — backend logic, per your instruction
- `app/api/*`, `app/core/*` — backend Python
- Other surfaces (`friends jarvis/`, `chatjimmy_research/`, `docs/`)

If you want me to dig into a specific surface (e.g., full map of every CSS class → JS hookup, or accessibility audit, or a concrete refactor plan for `script.js`), point me at it and I'll go deep.
