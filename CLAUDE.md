# CLAUDE.md — J.A.R.V.I.S Project Brief

> **Last updated:** 2026-08-01
> Single source of context for any AI agent working in this repo. **Read this FIRST.**
> Every file path, line number, singleton, and constant below was verified against the working tree.
> If something here contradicts the code, the code wins — fix this file.
>
> **Implementation history:** M0–M7 completed by Claude Code (Opus 5) on ~2026-07-27.
> M8 (Reminders, Notes & To-Do) implemented by Antigravity on 2026-07-29.
> M11 (Conversation History UI) implemented by Claude Code (Opus 5) on 2026-07-30.
> M12 (URL conversation routing) implemented by Claude Code (Opus 5) on 2026-07-30.
> **M13 (Understanding & Truthfulness) implemented 2026-08-01** — plan in
> `IMPLEMENTATION_PLAN_M13.md`. Phase 1 fixed the dead frontend ACK chain, the
> duplicated/mis-attributed frontend actions, the silent no-op, and verify-before-reply.
> Phase 2 replaced `BrainService` with `app/services/resolver.py` and deleted every
> hardcoded language list. **`brain_service.py` no longer exists.**
> M9 (SKILL.md skills) and M10 (MCP client) are NOT yet implemented.
>
> **Numbers in this file were re-measured on 2026-08-01** with
> `scripts/_m13_doc_audit.py` (line counts, 95 tools, 48 route entries, 25 test files,
> 8 databases). Re-run it before editing §1, §3, §5, §6, §13, §15 or §16 — do not
> hand-count, and do not trust `len(app.routes)` (§5).

---

## 1. What This Is

J.A.R.V.I.S is a **local, voice-enabled AI assistant for Windows** — a FastAPI server that controls the user's desktop, manages files, searches the web, integrates with Google services, and holds conversations. Runs on `localhost:8000`, used through a browser UI.

**Not** a library, framework, or SaaS product. A personal assistant on one Windows PC.

**Scale:** 24,721 lines across 80 Python files in `app/services/` alone (measured 2026-08-01 with `scripts/_m13_doc_audit.py`). Was ~19,400 / 69 files pre-M7.

---

## 2. Tech Stack

| Layer | Tech |
|---|---|
| Backend | Python 3.12, FastAPI, uvicorn |
| Agent LLM | Gemini 2.0 Flash (PRIMARY) → Groq/gpt-oss-120b (FALLBACK) |
| Chat LLM | Groq gpt-oss-120b + optional Gemini fallback |
| Brain/classify | Groq gpt-oss-20b / Gemini (optional race mode) |
| Memory extraction | Groq gpt-oss-20b (background, post-turn) |
| Vision | Groq Llama-4-Scout-17b or Gemini |
| LLM SDKs | `groq`, `openai` (OpenAI-compatible endpoints), LangChain (embeddings only) |
| TTS / STT | Edge TTS (`en-GB-RyanNeural`) / Groq Whisper |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` + FAISS |
| Desktop automation | pyautogui, pygetwindow, pywinauto (UIA), pywin32, comtypes |
| System control | pycaw (audio), screen-brightness-control, psutil, winsdk (radios + OCR), winreg |
| Google APIs | google-api-python-client (Gmail, Calendar, Drive) — **gmail.send scope added in M7** |
| Frontend | Vanilla HTML/CSS/JS + WebGL orb + GLSL shaders — **no framework** |
| Storage | 6 SQLite DBs under `data/` (5 original + `file_index.db` added M7) |

---

## 3. Architecture Map

```
Browser UI (web/)
   │  SSE
   ▼
FastAPI (app/main.py, 159 lines) ── 9 routers in app/api/
   │
   ├── app/core/startup.py       Lifespan: boots all services in dependency order
   ├── app/core/state.py         Global service references
   ├── app/core/streaming.py     SSE generator + inline TTS synthesis
   ├── app/core/middleware.py    TimingMiddleware (request timing + cache headers)
   │
   ├── ChatService (1674)         Main orchestrator — one chat turn end-to-end + conversation history (M11)
   │     ├── Resolver (600)              UNDERSTAND: raw utterance -> self-contained goal + kind (M13)
   │     ├── GroqService (512)           General chat + system prompt build
   │     ├── RealtimeService (638)       Serper web search + grounded answer
   │     ├── AgentLoop (856)             ReAct tool-calling loop (+ no-op guard, M13)
   │     └── VisionService (135)         Camera/image understanding
   │
   ├── Agent subsystem — app/services/agent/
   │     ├── tool_registry.py (272)      @tool decorator, ToolSpec, ToolRegistry
   │     ├── tools/ (16 files, ~4600+)   14 tool modules → 95 registered tools
   │     ├── execution/ (3, ~355)        ExecutionCoordinator (189) + typed contracts
   │     ├── cache/ (5, ~1280)           Verified command cache (Phase 6); coordinator.py 552
   │     ├── checker/ (9, ~1880)         Async verification + learning (Phase 4); coordinator.py 443
   │     ├── planner/ (4, ~869)          Multi-step planner + executor (Phase 5)
   │     ├── proactive/ (3, ~545)        Suggestion engine (Phase 7)
   │     ├── personalization/ (2, ~515)  User model + habits (Phase 8)
   │     ├── automation/ (4, ~1844)      Windows UIA engine + COM STA thread
   │     ├── file_index.py               SQLite file-search index (M7)
   │     ├── tool_result_store.py        Offloads large tool outputs to disk (M6)
   │     ├── action_sink.py (146)        Frontend action collector — drained PER ACTION (M13)
   │     └── deps.py (39)               Dependency injection (avoids circular imports)
   │
   ├── ReminderService (468)       heapq scheduler + SQLite persistence (M8)
   ├── NotesService (400)          Notes + To-Do lists with CRUD, SQLite (M8)
   │
   ├── Resolver (600)             Understanding layer — one LLM call per turn, before routing (M13)
   ├── MemoryService (693)        SQLite WAL, facts/actions/corrections + FTS5
   ├── MemoryExtractor            Background LLM fact extraction after each turn (M3)
   ├── MaintenanceService         Retention pruning + SQLite backups at startup (M2)
   ├── ContextEngine (802)        Reference resolution ("it"/"that"/"woh")
   ├── SystemStateWatcher (595)   Live Windows state daemon
   ├── llm_providers (229)        Key rotation, circuit breakers, failover
   ├── ApiKeyMonitor (483)        Per-key stats, latency, error rates
   ├── DebugLogger (566)          Per-turn structured JSON session logs
   ├── VectorStore (131)          FAISS retrieval index
   └── Google services (~803)     OAuth, Gmail, Calendar, Drive
```

---

## 4. Directory Structure

```
jarvis/
├── app/
│   ├── main.py (158)              FastAPI app, logging, CORS, router registration
│   ├── models.py                  Pydantic models (ChatRequest, ChatResponse, ChatMessage)
│   ├── _com_guard.py              Swallows harmless COM release crashes (pycaw/pywinauto __del__)
│   ├── api/                       system, chat, tts, dashboard, proactive, usermodel, testing, reminders (M8), notes (M8)
│   ├── core/                      startup.py, state.py, streaming.py, middleware.py, helpers.py
│   ├── services/
│   │   ├── resolver.py            UNDERSTANDING layer (M13). get_resolver(). Replaced brain_service.py
│   │   ├── db.py                  Shared SQLite helper: open_db(), checkpoint_and_close_all() (M1)
│   │   ├── maintenance.py         Retention pruning + daily SQLite backup (M2)
│   │   ├── memory_extractor.py    Background LLM fact extraction, post-turn (M3)
│   │   ├── reminder_service.py    heapq-based reminder scheduler + SQLite (M8)
│   │   ├── notes_service.py       Notes & To-Do CRUD + SQLite (M8)
│   │   ├── agent/
│   │   │   ├── tool_result_store.py   Offloads large tool results to data/tool_results/ (M6)
│   │   │   ├── file_index.py          SQLite index over user folders for find_file (M7)
│   │   │   └── tools/
│   │   │       ├── screen_tools.py    read_screen, screen_region_capture (M7, new module)
│   │   │       ├── reminder_tools.py  set_reminder, list_reminders, cancel_reminder, snooze_reminder (M8)
│   │   │       └── notes_tools.py     notes_manage, todo_manage, todo_item, note_correction (M8)
│   │   └── ...all other services unchanged
│   └── utils/                     key_rotation.py, retry.py, time_info.py
├── web/                           Browser UI — orb dashboard, status states, ambient light, scroll FAB (M5+UI)
├── data/
│   ├── *.db                       8 SQLite DBs (adds file_index.db, reminders.db, notes.db — M8)
│   ├── tool_results/              Offloaded large agent outputs (M6, pruned by M2)
│   └── backups/YYYY-MM-DD/        Daily SQLite .backup() snapshots (M2, 7-day keep)
├── scripts/
│   ├── verdict_report.py          Health report: verifier coverage, verdicts, WAL, latency (M0)
│   ├── _tag_verification.py       One-off helper used during M4 tool tagging
│   ├── _history_ui_check.py       One-off Playwright check for the history drawer (M11)
│   └── _m13_*.py                  M13 diagnostics: doc_audit, resolver_check, provider_probe,
│                                  live_turn, frontend_ack_check, honesty_check, graceful_stop (§16)
├── tests/                         347 passing tests in 25 files (215 before M13, 58 before M0–M7)
├── docs/
│   └── BASELINE.md                Committed baseline table from M0 measurement pass
├── config.py (536)                ALL configuration, env-driven (new sections for M1–M8, M11, M13)
├── run.py (40)                    Server entry point
├── start.bat                      Windows one-click launcher
├── requirements.txt               ~40 dependencies (pypdf, python-docx, openpyxl for M7)
├── implementation.txt             Approved plan (M0–M10)
├── implementation report v1.txt   Structured record of M0–M7 delivery
├── IMPLEMENTATION_PLAN_M13.md     Approved plan for M13 (understanding + truthfulness)
└── CLAUDE.md                      This file
```

**Deleted in M13:** `app/services/brain_service.py`. The five-category classifier and
its rule-based keyword fallback are gone; `app/services/resolver.py` replaces both.
Anything still importing `BrainService` is stale.

**Stale doc warning:** `README.md` and `implementation_plan.md` reference an OLD layout (`phase4/`, `phase6/`, `phase7/`, `phase8/`, `skills/`, `frontend/`). Real names are `checker/`, `cache/`, `proactive/`, `personalization/`, `google/`, `web/`. Trust CLAUDE.md and the code, not those docs.

---

## 5. API Endpoints (48 method+path entries across 42 paths, including the `/` redirect)

Counted 2026-08-01 with `scripts/_m13_doc_audit.py`: 47 router entries over 41 paths, plus
`GET /`. Don't hand-count — this FastAPI version wraps included routers in lazy
`_IncludedRouter` objects, so `len(app.routes)` reads 12 and is meaningless.

**M13 changes:** no routes added or removed. `GET /health` now reports `"resolver"` instead of
`"brain_service"`. `POST /api/activity/frontend-ack` is unchanged but **now actually does
something** — its ack finally reaches a registered dispatch (§10).

### Chat — `app/api/chat.py`
| Method | Path | Purpose |
|---|---|---|
| POST | `/chat/jarvis/stream` | **PRIMARY endpoint** — classify + route + execute, SSE |
| POST | `/chat` | Non-streaming general chat |
| POST | `/chat/stream` | Streaming general chat |
| POST | `/chat/realtime` | Non-streaming web-grounded chat |
| POST | `/chat/realtime/stream` | Streaming web-grounded chat |
| GET | `/chat/history` | **Conversation list** — newest-first summaries, `?query=&limit=&cursor=` (M11) |
| GET | `/chat/history/{session_id}` | Full conversation + metadata; `404` when missing (M11) |
| PATCH | `/chat/history/{session_id}` | Rename a conversation (M11) |
| DELETE | `/chat/history/{session_id}` | **Permanently** delete a conversation (M11) |

### System — `app/api/system.py`
| GET | `/health` | Health check (all services) |
| GET | `/api` | Endpoint listing |
| GET | `/api/key-monitor` | Provider/key usage stats |
| GET | `/api/startup-brief/stream` | Daily startup greeting (SSE) |

### TTS / STT — `app/api/tts.py`
| POST | `/tts` | Edge TTS, cached to `data/voice_cache/` |
| POST | `/transcribe` | Groq Whisper transcription |

### Dashboard — `app/api/dashboard.py`
| GET | `/dashboard` | Control Center HTML |
| GET | `/watcher` | Watcher dashboard HTML |
| GET | `/api/dashboard/state` | Aggregated state from all phases |
| GET | `/api/watcher/state` | Live system state snapshot |
| GET | `/api/activity/recent` | Phase 4 + Phase 6 activity feed (now includes human-readable `message` on FAIL rows) |
| POST | `/api/activity/frontend-ack` | Browser action acknowledgement |

### Proactive — `app/api/proactive.py`
| GET | `/api/proactive/pending` · POST `/accept` · POST `/dismiss` · POST `/consent` |

### User Model — `app/api/usermodel.py`
| GET | `/api/usermodel/knowledge` · POST `/api/usermodel/forget` |

### Testing — `app/api/testing.py`
| POST | `/api/test-session/run` (SSE) · GET `/api/test-session/{session_id}/logs` |

### Reminders — `app/api/reminders.py` *(M8)*
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/reminders` | List active reminders (filter: all/today/upcoming) |
| POST | `/api/reminders` | Create a reminder |
| DELETE | `/api/reminders/{id}` | Delete a reminder |
| POST | `/api/reminders/{id}/snooze` | Snooze a reminder by N minutes |
| POST | `/api/reminders/{id}/done` | Mark a reminder as done |
| GET | `/api/notifications/stream` | SSE stream for real-time reminder notifications |

### Notes & To-Do — `app/api/notes.py` *(M8)*
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/notes` | List notes (optional search query) |
| POST | `/api/notes` | Create a note |
| GET | `/api/notes/{id}` | Get a specific note |
| PUT | `/api/notes/{id}` | Update a note |
| DELETE | `/api/notes/{id}` | Delete a note |
| GET | `/api/todos` | List all to-do lists |
| POST | `/api/todos` | Create a to-do list |
| DELETE | `/api/todos/{id}` | Delete a to-do list |
| POST | `/api/todos/{id}/items` | Add items to a to-do list |
| PUT | `/api/todos/items/{id}/done` | Toggle item done/undone |
| DELETE | `/api/todos/items/{id}` | Delete a to-do item |

### Conversation deep links — `app/api/dashboard.py` *(M12)*
| GET | `/jarvis/c/{session_id}` | Serves the app shell for a client-side conversation URL |
| GET | `/app/c/{session_id}` | Same, for the `/app` mount |

**Static mounts:** `/jarvis` and `/app` → `web/`. `/` → 302 redirect to `/jarvis/`.
The two `c/{session_id}` routes are registered **with the routers, before the mounts**, so a hard refresh on a deep link hits the handler instead of 404ing on `StaticFiles`.

---

## 6. All 95 Registered Tools

Registered via `@tool(...)` in `app/services/agent/tools/`. Count is **95** (was 88 before M8) — never hardcode it; use `registry.names()`.

**Verification coverage (measured 2026-08-01, `scripts/_m13_doc_audit.py`):**
- **95/95** tools declare `verification=` (was 4/75 before M4)
- **VERIFIABLE: 85/95 (89%)** | BY DESIGN `family="none"`: 10 | UNCLASSIFIED: **0**
- 10 tools are `dangerous=True`: `calendar_delete`, `delete_file`, `empty_recycle_bin`, `gmail_send`, `hibernate_computer`, `kill_process`, `restart_computer`, `shutdown_computer`, `sign_out`, `sleep_computer`
- Family spread: `query` 28 · `file` 10 · `none` 10 · `memory` 9 · `toggle` 8 · `input` 7 · `ui` 7 · `frontend` 6 · `google` 5 · `open` 3 · `close` 2
- M13 changed no tool count. What it changed is that the 6 `frontend` tools **can now actually verify** (§10).

### `desktop_tools.py` — 13 tools *(+1 from M7)*
`open_application` · `close_application` · `type_text` · `press_hotkey` · `mouse_click` · `scroll` · `take_screenshot` · `set_clipboard` · `get_clipboard` · `focus_window` · `window_action` · `list_open_windows` · **`clipboard_history`** *(M7)*

Key internals (**M13 rewrote app-name resolution**): `_resolve_launch_target()` asks Windows what a name is — `shutil.which()` for anything on PATH (covers every built-in: `notepad`, `calc`, `mspaint`, `explorer`, `taskmgr`, `control`, `powershell`), then `_find_app_shortcut()` scans Start Menu `.lnk` files for AppData-installed apps (Telegram/Discord/Spotify/VS Code), then the bare name. It returns `(command, how)` where `how` is `path`/`shortcut`/`unknown`; an `unknown` name that produced no new process returns `ERROR: ...` naming `open_settings_page` / `open_website` as alternatives, instead of falsely claiming success. **`_APP_ALIASES` and `_CLOSE_PRONOUNS` were deleted** — the alias table was a language guess, and reference detection now has exactly one owner, `detect_reference()` in the context engine (used by `_looks_like_reference()` and `_resolve_window_reference()`). `open_application` snapshots PIDs before launch so `close_application("it")` works for unknown/UWP apps. `clipboard_history` reads from a bounded ring buffer (last 20 entries) maintained by the watcher tick — no separate poller.

### `uia_tools.py` — 8 tools
`ui_do` · `ui_click` · `ui_set_toggle` · `ui_type_into` · `ui_list_controls` · `ui_scroll` · `ui_wait` · `ui_diagnostics`

`ui_do` is the **primary** UI tool — it searches, drills into sections, scrolls, acts, then re-reads to confirm. `ui_list_controls` output is a likely candidate for tool-result offloading (M6) — up to 4000 nodes → saved to `data/tool_results/` instead of staying in the LLM context.

### `file_tools.py` — 11 tools *(+3 from M7, + path guard)*
`list_directory` · `read_file` · `write_file` · `open_file` · **`delete_file` (DANGEROUS)** · `create_folder` · `move_to_trash` · `move_path` · **`find_file`** *(M7)* · **`read_document`** *(M7)* · **`zip_files` / `unzip_file`** *(M7)*

Constants: `_MAX_READ=20000` (L18), `_USER_DIRS` (L24) resolves `desktop`/`downloads`/`documents` by name.

**`_guard_path()` (M7):** applied at the top of all 4 mutating file tools. Blocks writes/deletes under `C:\Windows`, `C:\Program Files*`, `C:\ProgramData`, and the JARVIS repo `BASE_DIR`. Returns `ERROR: ...` (never raises). This is an accident guard — if the LLM hallucinates a path, nothing silently destructive happens.

**`find_file`:** backed by `app/services/agent/file_index.py` — a SQLite index (`data/file_index.db`) built over user folders (Desktop/Documents/Downloads/Pictures/Videos/Music) at startup in a background thread. Max 200k files total; max 300 per directory (ML dataset cap, bug fixed during M7). Search is sub-second. Refresh hooks the existing watcher tick, not a new poller.

**`read_document`:** extracts text from PDF (pypdf), DOCX (python-docx), XLSX (openpyxl). Same `_MAX_READ` cap. Deps are optional imports — verify `requirements.txt` on fresh deploy.

**`zip_files` / `unzip_file`:** includes zip-slip path guard.

### `screen_tools.py` — 2 tools *(new module, M7)*
**`read_screen`** · **`screen_region_capture`**

`read_screen` uses `winsdk.windows.media.ocr` — already a dependency from the radios code. Returns extracted text, not an image. `screen_region_capture` saves a PNG crop (`family="file"` verifier). Imported in `tools/__init__.py`.

### `system_tools.py` — 11 tools *(+1 from M7)*
`set_volume` · `mute_volume` · `set_brightness` · `media_control` · `lock_screen` · **`shutdown_computer`** · **`restart_computer`** · **`sleep_computer`** · `cancel_power_action` · `camera_control` · **`app_volume`** *(M7)*

`camera_control` flips the HKCU webcam ConsentStore registry value and **reads it back to self-verify** (`family="toggle"`, `capability="privacy.camera"`). `app_volume` controls per-application volume via pycaw `AudioUtilities.GetAllSessions()` — must run on the existing audio STA worker (`tools/_audio.py`), never the UI apartment.

### `system_info_tools.py` — 12 tools
`battery_status` · `system_resources` · `list_processes` · **`kill_process`** · `get_datetime` · `network_info` · `list_wifi_networks` · `connect_wifi` · `open_settings_page` · **`empty_recycle_bin`** · **`hibernate_computer`** · **`sign_out`**

`_SETTINGS_URIS` (L314) maps page names → `ms-settings:` deep links. All read-only info tools are tagged `family="query"`, `cacheable=False` (volatile — `battery_status`, `get_datetime` etc. must never be replayed from cache).

### `settings_tools.py` — 6 tools *(+1 from M7)*
`wifi_control` · `bluetooth_control` · `get_system_status` · `airplane_mode` · `check_for_updates` · **`bluetooth_connect_device`** *(M7)*

Uses `winsdk.windows.devices.radios` async API for real radio state; falls back to UI navigation. `bluetooth_connect_device` pairs/connects a named device (not just the toggle).

### `web_tools.py` — 4 tools
`open_website` · `play_on_youtube` · `search_google` · `search_youtube`

These emit **frontend actions** (browser opens them), not server-side effects. Tagged `family="frontend"` — verifier joins frontend ACK from `POST /api/activity/frontend-ack`.

**M13 removed `try_direct_web_command` and `_SITE_MAP`.** There is no regex fast path and no
site-name table any more. `_normalize_url()` is now purely mechanical: scheme, dotted
hostname, `localhost`/`127.0.0.1`, or a bare name given the conventional `www.<name>.com`
shape. Speed for repeated web commands comes from the verified cache instead (§8 Phase 6) —
and as of M13 these tools **can finally verify**, so they actually get promoted.

### `google_tools.py` — 12 tools *(+3 from M7)*
`gmail_inbox` · `gmail_unread` · **`gmail_send` (DANGEROUS)** *(M7)* · `calendar_list` · `calendar_search` · `calendar_create` · **`calendar_update`** *(M7)* · **`calendar_delete` (DANGEROUS)** · `drive_search` · `drive_list` · `drive_upload` · **`drive_download`** *(M7)*

`gmail_send` requires `gmail.send` OAuth scope (added M7). **If `data/google_token.json` still exists from before M7, delete it and re-run OAuth** — the old read-only token will reject send calls. `calendar_update` and `calendar_delete` use `family="google"` verifier that re-queries the API to confirm the effect.

### `reminder_tools.py` — 4 tools *(new module, M8)*
**`set_reminder`** · **`list_reminders`** · **`cancel_reminder`** · **`snooze_reminder`**

Backend: `app/services/reminder_service.py` — heapq-based background scheduler + SQLite (`data/reminders.db`). Supports one-time and recurring reminders (daily/weekdays/weekly/monthly). `on_fire` callback pushes to SSE `NotificationBus`. The tool `set_reminder` understands natural language times relative to IST (e.g., "in 30 minutes", "tomorrow at 9am").

### `notes_tools.py` — 4 tools *(new module, M8)*
**`notes_manage`** · **`todo_manage`** · **`todo_item`** · `note_correction`

Backend: `app/services/notes_service.py` — Notes + To-Do lists with CRUD, SQLite (`data/notes.db`). `notes_manage` handles create/edit/delete/search/pin. `todo_manage` handles list-level CRUD. `todo_item` handles add/done/undone/remove for individual checklist items. All tools use `action_sink.set_panel()` to push UI panel open/refresh actions to the frontend.

### `memory_tools.py` — 3 tools *(was 4; `note_correction` moved to notes_tools.py)*
`remember` · `recall` · `forget`

Tagged `family="memory"` — verifier re-queries `memory.db` to confirm the fact was written/deleted.

### `content_tools.py` — 2 tools
`generate_image` (Pollinations) · `write_content` (via GroqService)

### `planner_tools.py` — 1 tool
`do_multistep` — entry point to Phase 5 planner. Tagged `family="none"` (genuinely unverifiable as a unit).

### The 10 DANGEROUS tools (require scoped confirmation)
`delete_file` · `shutdown_computer` · `restart_computer` · `sleep_computer` · `hibernate_computer` · `kill_process` · `sign_out` · `empty_recycle_bin` · `calendar_delete` · **`gmail_send`** *(M7)*

Verify with: `grep -rn "dangerous=True" app/services/agent/tools/`

---

## 7. Adding a New Tool

The **only** wiring needed:

```python
from app.services.agent.tool_registry import tool

@tool(
    name="my_tool",
    description="What it does — the LLM reads this.",
    params={"level": {"type": "int", "description": "0-100", "required": True}},
    dangerous=False,          # True → confirmation gate
    category="system",        # desktop | system | web | google | content
    verification={
        "family": "toggle",           # open|close|toggle|ui|file|input|query|frontend|memory|google|none
        "capability": "system.volume",  # optional — watcher capability to read back
        "target_key": "path",           # optional — overrides _TARGET_KEYS lookup
        "cacheable": False,             # set False for time-varying reads (battery, datetime, etc.)
    },
)
def my_tool(level: int) -> str:
    return f"Set to {level}."   # Return a string; "ERROR: ..." signals failure
```

Put it in an existing `tools/*.py`, or a new module imported in `tools/__init__.py`. The agent loop picks it up automatically — **no changes anywhere else**.

**`verification=` is mandatory.** `test_verifier_coverage.py` asserts every registered tool resolves to a non-None family — it will fail CI if you skip it.

**Canonical families:**
| Family | When | Verifier used |
|---|---|---|
| `open` | Opens an application or window | `_verify_open` — watcher process/window check |
| `close` | Closes an application or window | `_verify_close` |
| `toggle` | State change (radio, camera, setting) | `_verify_toggle` — watcher capability read-back |
| `ui` | UIA interaction | `_verify_ui` |
| `file` | Creates/moves/deletes a file/folder | `_verify_file` — path existence check |
| `input` | Types / sets clipboard | `_verify_input` |
| `query` | Read-only; PASS when no ERROR | `_verify_query` — transport OK |
| `frontend` | Browser action via ACK | `_verify_frontend` — joins activity dispatch ACK |
| `memory` | Memory read/write/delete | `_verify_memory` — re-queries memory.db |
| `google` | Calendar/Drive API mutation | `_verify_google` — re-queries the API |
| `none` | Genuinely unverifiable | Honest UNKNOWN; never cached |

Rules: never raise (registry catches, but return readable strings); prefix failures with `ERROR:` since that's how the loop and checker detect failure; only declared params are passed through (hallucinated extras are dropped by `registry.execute`).

---

## 8. Intelligence Phases

All optional, all fail-soft, all flag-gated.

| Phase | Directory | Singleton | Purpose | Flag |
|---|---|---|---|---|
| **0 Understanding (M13)** | `resolver.py` | `get_resolver()` | Utterance → self-contained goal + kind. Runs before routing. | `RESOLVER_ENABLED` |
| 1 Watcher | `watcher/` | `get_watcher()` | Live processes, windows, clipboard, settings | always on |
| 2 Memory | `memory_service.py` | `get_memory()` | Facts, actions, corrections (SQLite FTS5) | `MEMORY_ENABLED` |
| 2b Memory Extraction | `memory_extractor.py` | `get_memory_extractor()` | Background LLM fact extraction post-turn (M3) | `MEMORY_EXTRACT_ENABLED` |
| 3 Context | `context/` | `get_active_registry()` | Resolve "it"/"that"/"woh" → real entity | always on |
| 4 Checker | `checker/` | `get_phase4()` | Async PASS/FAIL/UNKNOWN verification + learning | `PHASE4_ENABLED` |
| 5 Planner | `planner/` | `get_phase5()` | Multi-step plan + sequential verified execution | `PHASE5_ENABLED` |
| 6 Cache | `cache/` | `get_cache_coordinator()` | Verified command replay + promotion policy | `PHASE6_ENABLED` |
| 7 Proactive | `proactive/` | `get_phase7()` | Watcher events → suggestions (never auto-act) | `PHASE7_ENABLED` |
| 8 User Model | `personalization/` | `get_phase8()` | Habits, aliases, preferences | `PHASE8_ENABLED` |

### Phase details worth knowing

**Understanding / Resolver** (`resolver.py`, M13): one LLM call per turn, **before** routing.
It substitutes for `BrainService.classify_primary`, so it is cost-neutral. Inputs: the raw
utterance, the last `RESOLVER_MAX_HISTORY_TURNS` (8) turns of **both** sides, the live state
block, the last action of the session **with its verdict**, relevant memory facts, and whether
a confirmation is pending. Output is strict, validated JSON:

```json
{"goal": "play the song Ishq by Fahim Abdullah on YouTube",
 "kind": "action|web_question|knowledge_question|visual|mixed",
 "self_contained": false, "refers_to_previous": true,
 "is_confirmation": null, "visual_source": null,
 "unresolved": [], "confidence": 0.0}
```

- **`goal`** is what reaches the agent loop — never the raw utterance. "Search for it." became
  `search_google(query="it")` before M13; it now becomes *"search for Ishq by Fahim Abdullah"*.
- **`kind`** replaces the five-category classifier. `ACTING_KINDS = (action, mixed, visual)` all
  go to the agent, because only it can act or look.
- **`self_contained`** is the sole input to cache eligibility (§4.4 / Phase 6).
- **`refers_to_previous`** replaces `_is_retry_complaint`.
- **`is_confirmation`** (`true`/`false`/`null`) replaces `_is_affirmative` / `_is_negative`.
- **`visual_source`** (`camera`/`screen`/`null`) is an addition to the plan's contract: the
  camera route needs a browser capture while an on-screen question needs the screen tools, and
  the model is the right place to decide — the alternative was another keyword list.
- **`unresolved`** non-empty ⇒ **ask instead of guessing**, but only for `ACTING_KINDS`.
  Guessing is dangerous before an action and merely unhelpful before a search, so a vague
  question ("who won the match last night") is searched rather than interrogated.

Providers are Gemini → Groq, **fixed order, never raced** (Rule #5), capped at
`RESOLVER_MAX_FAILOVER_KEYS` (3) keys per provider so an outage cannot stall every turn.
Fail-soft ladder: unparseable JSON ⇒ one re-ask ⇒ raw utterance as the goal with
`kind=action` (`source="fallback"`, not shown as "understood" in the UI); no provider at all
⇒ `source="offline"` ⇒ try the verified cache (needs no LLM) ⇒ otherwise an honest "I can't
reach my reasoning engine". **Never keyword guessing.** Typical latency 2–4s.

**Watcher** (`state_service.py`): `note_launch` (L342) diffs PIDs before/after launch → real process tracking. `close_by_name` (L469) closes the tracked PID, which is why "close it" works for UWP apps. `_emit_events` (L159) diffs snapshots and publishes to the event bus — **do not add a second poller**. Also maintains a bounded ring buffer of the last 20 clipboard entries for `clipboard_history`.

**Memory Extractor** (`memory_extractor.py`, M3): runs a background daemon thread; `submit(user_msg, reply)` is called after each turn completes — never blocks the chat path. Uses `gpt-oss-20b` to extract up to `MEMORY_EXTRACT_MAX_FACTS_PER_TURN` (3) facts as strict JSON `{"facts":[{"category","key","value"}]}`. Reuses `MemoryService._looks_secret()` and `remember()` upsert. Mirrors user facts into Phase 8 `um_facts`. The old `auto_capture()` regex path in `memory_service.py` is retained as a fast zero-cost first pass.

**Context** (`context_engine.py`): salience scoring weights at L303-308 — focus 5.0, mention 4.0, recency 3.0, tool-result 2.0, type-match 1.0, recency half-life 120s. Registry is **thread-local** (`set_active_registry` L744) so concurrent sessions can't leak.

**Checker** (`checker.py`, M4): `classify_family()` now reads `registry.get(tool).verification["family"]` **first** (metadata-driven), falling back to legacy name heuristics. New verifiers: `_verify_query` (read-only tools), `_verify_memory` (memory tools), `_verify_google` (Calendar/Drive mutations), `_verify_frontend` (web tools via ACK), `_verify_gmail` (Sent-folder check for `gmail_send`). `CHECKER_SETTLE_PROFILES` (config L331) defines per-family settle timing.

**Cache** (`coordinator.py`): 4 lookup layers — L1 exact normalized, L2 signature/paraphrase, L3 FAISS semantic, L4 static response. `signature.py` keeps polarity: "wifi on" ≠ "wifi off". `_is_uncacheable()` respects `cacheable=False` in tool `verification=` metadata — volatile reads never promote.

**M13 added a second promotion gate: `_is_self_contained()`.** The chat layer calls
`note_eligibility(command, self_contained)` from the resolver's flag *before* anything runs, and
promotion requires it to be True (`CACHE_REQUIRE_SELF_CONTAINED`, default on). An **unrecorded**
command is never promoted — not knowing whether a phrasing depends on context is not a licence
to replay it later. So "close it" can verify PASS a hundred times and still never be cached.
This is what makes speed *earned*: a phrasing you use repeatedly, proven to work, replays
instantly; nothing is fast because someone wrote a rule for it.

A replay carries no stored sentence (the verdict that promotes an entry arrives before the reply
is composed), so `_reply_from_results()` speaks the tool's own observation rather than "Done.".
Replays are verified like any other execution, so a stale entry cannot report success.

**Planner** (`planner.py`): `plan_shortfall` (L132) rejects plans that only open something without acting — `_OPEN_ONLY_TOOLS` (L125) vs `_CHANGE_VERBS` (L110). Max 8 steps.

**Learner** (`learner.py`): retries only `_IDEMPOTENT_SAFE` (L41) tools; never retries `_IRREVERSIBLE` (L35). Max 2 retries. **M13 added module-level `retry_is_safe(tool)`**, shared by the Learner and by the turn-level retry (§3.4), so there is exactly one definition of "safe to do twice". It decides from the tool's own metadata — not dangerous, and `verification.family` in `_IDEMPOTENT_FAMILIES = {open, close, toggle, query, frontend}` — so a newly registered tool is classified correctly the moment it exists. `input`, `file` and `ui` are excluded on purpose: typing or saving twice is a real duplicate effect, and a FAIL may be false.

**Checker → verdict waiting** (`checker/coordinator.py`, M13): `wait_for_verdict(action_id, timeout)` lets a turn block briefly on the truth. Verdicts are recorded by `action_id` in `_publish_verdict_locally()` (called from the existing `verified` bus subscription) and a `threading.Event` per waiter is woken. Returns `None` on timeout, which the caller treats as UNKNOWN — **never** as success.

**UI thread** (`ui_thread.py`): all COM/UIA calls are serialized onto one dedicated STA thread with a message pump. Apartment retires after `_MAX_GENERATIONS=8` to dodge pywinauto COM leaks. Audio uses a *separate* STA worker (`tools/_audio.py`) so a hung UI call can't block volume.

**Tool result offloading** (`tool_result_store.py`, M6): `agent_loop.py` calls `maybe_offload()` before appending any tool observation to the message context. If `len(observation) > AGENT_TOOL_RESULT_MAX_CHARS` (default 4000), the full text is written to `data/tool_results/<execution_id>/<action_id>.txt` and the context gets a short pointer instead. `read_file` is exempt to avoid persist→read→persist loops. Full observation still used for verification and activity preview.

**Conversation History** (`chat_service.py` + `web/script.js`, M11): chat JSON files stay the source of truth. Saved documents now carry `schema_version`, `title`, `title_is_custom`, `created_at`, `updated_at`, `message_count` alongside `messages`; the old `{session_id, messages}` shape still loads, with title derived from the first user message and timestamps from filesystem metadata. `_load_meta_for_save()` keeps a per-session metadata cache so a user rename and the original `created_at` survive the every-5-chunks resaves; `_atomic_write_json()` (temp file + `os.replace`) means an interrupted write can no longer leave partial JSON. `list_conversations()` scans the directory, skips unreadable files with a log instead of breaking the sidebar, and paginates on a stable `(updated_at, session_id)` cursor. **`session_id` is always read from inside the file, never parsed back out of the filename** — the filename strips dashes and is not reversible. Frontend state lives in `historyState` with a `requestToken` so a slow fetch can never render over a newer one; the active session id is written to the URL through `setActiveSession()` (see **URL routing** below) and restored from it on refresh. Reopening a conversation deliberately does **not** rebuild the Activity panel — chat files store only user/assistant messages, so showing stale telemetry as current would be a lie.

**Transient sessions** (`chat_service.py`, M11 fix): `ChatService._transient_sessions` holds session ids that exist only to drive a stream and must never be written to disk. The daily startup brief is the only member — its "user message" is an internal prompt, so persisting it made the raw prompt show up as a `You` bubble and as the conversation title in the history sidebar. `save_chat_session()` early-returns for transient ids (guarding there, not at the call sites, also covers the save-all-sessions pass on shutdown) and `get_conversation()` returns `None` for them. `GET /api/startup-brief/stream` ignores any caller-supplied `session_id` and always allocates a fresh session, so marking one transient can never silence a real conversation's saves.

**Failure reporting** (`web/script.js`, M5): `startBackgroundActivityPolling` now checks for `row.verdict === 'FAIL'`. When found, `addCorrectionMessage()` appends a visually distinct "Jarvis (correction)" bubble to the chat thread — not just the side panel. PASS/UNKNOWN stay side-panel only to avoid noise.

---

## 9. Startup Sequence (`app/core/startup.py`)

Order matters — later steps depend on earlier ones:

1. System State Watcher (background daemon)
2. Persistent Memory (SQLite warm) — now uses `open_db()` from `db.py` (M1)
3. Embedding model load (CPU) → VectorStore
4. LLM services: GroqService, RealtimeService, **Resolver** (`get_resolver()`, M13 — replaced BrainService), VisionService
5. Agent: `load_all_tools()` → AgentLoop (Gemini primary + Groq fallback)
6. Phase 4: Checker + Vision + SkillMaker + Learner
7. Phase 5: Planner/Executor coordinator
8. UI Automation: COM apartment + UIA backend warmup
9. Phase 6: Verified command cache
10. Phase 7: Proactive engine
11. Phase 8: User model → wires habits into Phase 7
12. ChatService (depends on everything above)
13. *(M3)* Memory Extractor daemon thread starts
14. *(M2)* `run_startup_maintenance()` — prune logs, voice cache, stale files; SQLite backup
15. *(M7)* File index background build (fail-soft)

**Shutdown** (reverse order): TTS pool → watcher → Phase 8→7→6→5→4 → save chats → **`checkpoint_and_close_all()`** (M1, flushes WAL and closes all registered connections cleanly).

---

## 10. Execution Pipeline

```
User request
  → Resolver.resolve()   ONE LLM call: goal + kind + self_contained + refers_to_previous
  │                      + is_confirmation + visual_source + unresolved       (M13)
  │    → activity event "understood: <goal>"           shown every turn (§4.6)
  │    → source == "offline"  → verified cache replay, else honest error. NO keyword guessing.
  │    → confirmation pending → is_confirmation decides grant / cancel / ask again
  │    → unresolved && acting → ask a generated clarifying question, end turn
  │    → STATE GUARD: last action verdict FAIL/UNKNOWN && refers_to_previous
  │                   → forced to the agent even if kind said chat
  ├── kind == knowledge_question           → GroqService  (owner's own wording)
  ├── kind == web_question                 → RealtimeService (the RESOLVED goal)
  ├── kind == visual, source == camera     → camera route
  └── kind in (action, mixed, visual)      → agent, driven by GOAL, expect_action=True
        → phase6.note_eligibility(goal, self_contained)   cache gate, set BEFORE running
        → CacheCoordinator.lookup()       L1 exact → L2 signature → L3 FAISS → L4 static
        → HIT  → replay verified actions (order preserved, re-check danger), then verify
        → MISS → AgentLoop (LLM + 95 tools, max 16 steps)
                   └─ empty completion (no text, no tool call) → provider FAILURE, fail over
                   └─ no tool call on an action turn → NUDGE once, then admit. Never "Done."
                   └─ every tool → ExecutionCoordinator.execute_action()
                        1. registry.validate_arguments()  → schema check
                        2. requires_confirmation?          → ConfirmationRequired
                        3. registry.execute()              → run once
                        4. attach_dispatch() + phase4.register_dispatch()   ← M13 §3.1
                        5. action_sink.collect(drain=True) → THIS action's payload only
                        6. memory.record_action()          → with execution_id + action_id
                        7. phase4.publish_action_done()    → async verify queued
                   └─ large observations → tool_result_store.maybe_offload() (M6)
        → ExecutionCoordinator.complete() → publish_execution_completed(manifest)
        → VERIFY BEFORE REPLY (M13 §3.4): wait_for_verdict() per action, one shared
          VERIFY_WAIT_TIMEOUT budget for the turn
             → all PASS  → say the draft as-is
             → FAIL      → re-enter the agent ONCE with the reason (idempotent tools only)
             → FAIL again / UNKNOWN / timeout → reply states what could NOT be confirmed
          (a `mixed` turn settles its action here too, and the caveat LEADS the
           conversational answer — otherwise mixed would be the one route where an
           action could fail silently behind a confident-sounding reply)
        → Phase 6 joins verdicts with manifest → promote (needs self_contained) / evict
        → Phase 8 ingests verified actions → habits
        → Phase 7 may create a suggestion (never auto-acts)
  → After stream ends: MemoryExtractor.submit(user_msg, reply) → background fact extraction (M3)
```

**Browser ACK chain (was dead until M13).** `attach_dispatch()` returns the `dispatch_id` it
mints; `ExecutionCoordinator._drain_frontend_actions()` registers it with Phase 4 **inside
`execute_action`**, strictly before the chat layer yields `_actions`, so the browser can never
ACK a dispatch the coordinator has not heard of. `register_dispatch` existed before but was
never called from anywhere, so `acknowledge_dispatch` always popped `None`, `_dispatch_results`
was never written, and `_verify_frontend` returned `UNKNOWN: browser never acknowledged the
action` for **every web tool, permanently** — which is why `command_cache` stayed nearly empty.
The sink is now drained **per action**, so each frontend action carries its own `_meta` (own
`dispatch_id`) and a multi-step run can neither re-open step 1's tab nor lose step 1's ACK.

### Typed contracts — `app/services/agent/execution/models.py` (163 lines, `SCHEMA_VERSION = 1`)

| Contract | Line | Key fields |
|---|---|---|
| `ConfirmationGrant` | 54 | `action_id`, `tool`, `args_hash`, `expires_at`, `.valid()` |
| `ExecutionContext` | 66 | `execution_id`, `turn_id`, `session_id`, `route`, `source`, `confirmation_grants` |
| `ActionSpec` | 80 | `action_id`, `tool`, `args`, `index`, `risk_level`, `requires_confirmation`, `verification_barrier` |
| `ActionResult` | 96 | `transport_ok`, `observation`, `frontend_actions`, `error_type` |
| `VerificationResult` | 116 | `verdict` (PASS/FAIL/UNKNOWN), `evidence`, `source`, `confidence` |
| `ExecutionManifest` | 143 | ordered specs + results + verifications, `.ok`, `.step_payloads()` |

`event_envelope()` (L28) stamps every cross-module event with `schema_version`, `event_id`, `occurred_at`.

**`transport_ok` means the tool call was accepted — NOT that the effect happened.** Only a `VerificationResult` with `verdict == PASS` means it worked.

---

## 11. LLM Providers

**Agent (tool-calling) — order is FIXED and NEVER raced:**
1. **Gemini 2.5 Flash (PRIMARY)** — best tool-calling reliability
2. **gpt-oss-120b via Groq (FALLBACK)** — multi-key rotation. **See the TPM ceiling below.**

Special case in `agent_loop.py`: a Groq HTTP 400 "tool call validation failed" is a **model output bug, not a key problem** — every key on that model fails identically, so it breaks out immediately and fails over to Gemini instead of burning all keys.

**Three M13 additions, all from measurements in `scripts/_m13_provider_probe.py`:**

- **An empty completion is a provider failure, not an answer.** `_is_usable()` rejects a message
  with neither text nor a tool call and moves to the next key. Accepting one is how a turn ends
  up reporting nothing having done nothing — observed live on *"what's my battery level"*, where
  Gemini returned `content=''` with no tool calls twice in a row.
- **`AGENT_REASONING_EFFORT="none"`** (config). `gemini-2.5-flash` is a thinking model and its
  thinking tokens come from the **same** output budget as the answer, so a 4.7 KB system prompt
  plus 95 tool schemas (~10 k prompt tokens) could consume the whole budget. With thinking off it
  still selects the right tool on the first attempt in 17 completion tokens. Also raised
  `AGENT_MAX_OUTPUT_TOKENS` 1024 → 3000. If an endpoint rejects the parameter the loop drops it
  for the process rather than failing every key.
- **The Groq agent fallback cannot actually run on the free tier.** Measured: the agent request
  is ~8.1 k tokens against a `Limit 8000` TPM cap → HTTP 413 `rate_limit_exceeded` on *every*
  key. `Requested > Limit` is not transient and not key-specific, so the loop now breaks
  immediately with one clear error instead of burning all 8 keys. **Gemini is effectively the
  only usable agent provider** until the Groq tier is raised (see §18).

**Z.ai/GLM is DISABLED** — `self._zai_clients = []` (agent_loop.py L136). Code preserved for re-enable.

**General chat:** Groq + optional Gemini fallback. **Resolver:** Gemini → Groq, never raced, max `RESOLVER_MAX_FAILOVER_KEYS` keys per provider. **Vision:** Groq Llama-4-Scout or Gemini. **Memory extraction (M3):** `gpt-oss-20b` via Groq, configurable via `MEMORY_EXTRACT_MODEL`.

All providers: multi-key rotation (`GROQ_API_KEY_2`, `_3`, ...), per-key circuit breakers, rate-limit detection, `PROVIDER_COOLDOWN_SECONDS=30`.

---

## 12. Configuration (`config.py`, 536 lines)

All from `.env` via python-dotenv. `_env_bool()` parses booleans.

### Keys (append `_2`, `_3`, ... for rotation)
`GROQ_API_KEY` · `GEMINI_API_KEY` · `SERPER_API_KEY` · `ZAI_API_KEY` (disabled)

### Models
| Var | Default |
|---|---|
| `GROQ_MODEL` | `openai/gpt-oss-120b` |
| `AGENT_MODEL` | `openai/gpt-oss-120b` |
| `GEMINI_MODEL` | `gemini-2.0-flash` (live `.env` sets `gemini-2.5-flash`) |
| `GROQ_BRAIN_MODEL` | `openai/gpt-oss-20b` |
| `GEMINI_BRAIN_MODEL` | `gemini-2.0-flash` (live `.env` sets `gemini-flash-latest`) |
| `RESOLVER_MODEL` | defaults to `GEMINI_BRAIN_MODEL` (M13) |
| `GROQ_VISION_MODEL` | `meta-llama/llama-4-scout-17b-16e-instruct` |

### Timeouts / limits
`AGENT_MAX_STEPS=16` · `AGENT_REQUEST_TIMEOUT=18` · `AGENT_STEP_TIMEOUT=30` · `BRAIN_CLASSIFY_TIMEOUT=4` · `GEMINI_REQUEST_TIMEOUT=30` · `TASK_EXECUTION_TIMEOUT=30` · `PLANNER_MAX_STEPS=8` · `MAX_CHAT_HISTORY_TURNS=20` · `MAX_MESSAGE_LENGTH=32000`

### UIA
`UIA_ENABLED=True` · `UIA_LIBRARY=pywinauto` · `UIA_FIND_TIMEOUT=5.0` · `UIA_OP_TIMEOUT=20.0` · `UIA_WINDOW_WAIT=3.0` · `UIA_MAX_NODES=4000` · `UIA_NAV_MAX_STEPS=6`

### Tool result offloading (M6)
`AGENT_TOOL_RESULT_MAX_CHARS=4000` — observations larger than this are written to `data/tool_results/` and replaced with a pointer in the LLM context.

### Understanding layer (M13)
| Var | Default | Meaning |
|---|---|---|
| `RESOLVER_ENABLED` | `True` | Off ⇒ every turn is `source="offline"`: verified cache replay or an honest error. There is no keyword fallback to go back to. |
| `RESOLVER_MODEL` | `GEMINI_BRAIN_MODEL` | |
| `RESOLVER_TIMEOUT` | `6` | Per attempt. |
| `RESOLVER_MAX_HISTORY_TURNS` | `8` | Turns of context, both sides. |
| `RESOLVER_MAX_FAILOVER_KEYS` | `3` | Keys tried per provider before failing over. Stops a provider outage costing ~4s × every key — the cause of the old 14–22s classify times. |
| `RESOLVER_SHADOW_MODE` | `False` | Reserved: log the resolution without obeying it. |

### Truthfulness (M13)
| Var | Default | Meaning |
|---|---|---|
| `VERIFY_BEFORE_REPLY` | `True` | Wait for the verdict before composing the reply. Off ⇒ pre-M13 behaviour (late correction bubble only). |
| `VERIFY_WAIT_TIMEOUT` | `3.0` | Seconds, **per turn** not per action. Timeout ⇒ UNKNOWN ⇒ admit, never retry. |
| `AGENT_RETRY_ON_FAIL` | `True` | One retry on a verified FAIL, idempotent tools only. |
| `AGENT_NO_OP_NUDGES` | `1` | Nudges before an action turn with zero tool calls admits it did nothing. Nudges do **not** consume `AGENT_MAX_STEPS`. |
| `CACHE_REQUIRE_SELF_CONTAINED` | `True` | Only self-contained commands may be promoted. |
| `FRONTEND_DISPATCH_MAX_AGE` | `120` | Un-acknowledged dispatches swept after this, so `_pending_dispatches` cannot grow without bound. |
| `AGENT_MAX_OUTPUT_TOKENS` | `3000` | Output budget per agent step (was hardcoded 1024). |
| `AGENT_REASONING_EFFORT` | `none` | Thinking budget for the agent's Gemini calls. `""` omits the parameter. |

### Conversation History UI (M11)
`HISTORY_PAGE_SIZE=30` · `HISTORY_MAX_PAGE_SIZE=100` · `HISTORY_TITLE_MAX_CHARS=120` · `HISTORY_PREVIEW_MAX_CHARS=160` · `HISTORY_SEARCH_MAX_CHARS=200`

These bound the sidebar only. **`MAX_CHAT_HISTORY_TURNS` is unrelated** — it caps what the LLM receives, while the UI shows the whole persisted transcript.

### Memory extraction (M3)
`MEMORY_EXTRACT_ENABLED=True` · `MEMORY_EXTRACT_MODEL` (defaults to `INTENT_CLASSIFY_MODEL`) · `MEMORY_EXTRACT_MAX_FACTS_PER_TURN=3` · `MEMORY_EXTRACT_MIN_MESSAGE_CHARS` · `MEMORY_EXTRACT_TIMEOUT`

### Retention & backup (M2)
`MAINTENANCE_ENABLED=True` · `RETENTION_DEBUG_LOG_DAYS=7` · `RETENTION_VOICE_CACHE_FILES=5000` · `RETENTION_VOICE_CACHE_MB=500` · `BACKUP_KEEP_DAYS=7`

### Storage (M1)
`DB_WAL_AUTOCHECKPOINT_PAGES=1000` · `DB_BUSY_TIMEOUT_MS=5000`

### Safety-critical flags — **do not flip without reading §14**
| Flag | Default | Meaning |
|---|---|---|
| `CACHE_SEMANTIC_ENABLED` | `True` | Semantic retrieval on |
| `CACHE_SEMANTIC_AUTO_EXECUTE` | **`False`** | Semantic match must NOT auto-execute |
| `CACHE_SEMANTIC_THRESHOLD` | `0.93` | Similarity floor |
| `PROACTIVE_AUTO_ACT` | **`False`** | Suggestions only, never act |
| `MEMORY_REDACT_SECRETS` | `True` | Strip secret-like values |
| `CONTEXT_INCLUDE_CLIPBOARD_IN_PROMPT` | `False` | Clipboard stays out of prompts |
| `PHASE5_CONFIRM_RISKY` | `True` | Planner pauses on risky steps |
| `CONFIRMATION_GRANT_EXPIRY_SECONDS` | `120` | Grant lifetime |

### Learning thresholds
`SKILL_MIN_REPEATS=3` · `HABIT_MIN_OBSERVATIONS=3` · `CACHE_WEAK_EVIDENCE_MIN_OBS=3` · `CACHE_MAX_ENTRIES=500` · `LEARNER_MAX_RETRIES=2` · `CHECKER_SETTLE_SECONDS=1.5` · `PROACTIVE_MIN_INTERVAL_SECONDS=45`

### Personalization
`ASSISTANT_NAME="Jarvis"` · `JARVIS_OWNER_NAME` · `JARVIS_USER_TITLE` · `TTS_VOICE="en-GB-RyanNeural"` · `TTS_RATE="+22%"`

---

## 13. Frontend (`web/`, ~9515 lines across these five files)

| File | Lines | Purpose |
|---|---|---|
| `script.js` | 3583 | Chat logic, SSE parsing, PTT voice, camera, activity panel, orb dashboard, status badge states, scroll FAB, `handleActions()`, correction bubbles (M5), history drawer controller (M11), **M13 activity events: `understood` / `verifying` / `verdict` / `retrying` / `no_op_rejected`** |
| `style.css` | 4470 | Dark glassmorphism, ambient light, star field, staggered animations, responsive (768/480 breakpoints), **history drawer + dialogs (M11)** |
| `viewer.html` | 525 | Content/image viewer for background tasks |
| `index.html` | 478 | Main chat UI shell + scroll FAB + orb dashboard panel + **history drawer & dialogs (M11)** |
| `orb.js` | 459 | WebGL orb (GLSL shaders, simplex noise) + `setProperty()` / `setStateInstant()` / `applyGlobals()` |
| `api-monitor.js/html` | 191 | API key usage dashboard |

**Design tokens:** `--bg: #050510` · `--accent: #7c6aef` · `--accent-secondary: #4ecdc4` · `--glass-bg: rgba(10,10,28,0.72)`

**Voice:** push-to-talk (hold Ctrl+Shift) — Web Speech API fast path, Whisper `/transcribe` fallback.

**SSE event types:** text chunks · base64 audio · activity events · search results · frontend actions · background tasks.

### SSE activity contract — read this before touching `appendActivity()`

`ACTIVITY_STEPS` maps an `event` name to a label; the long `else if` chain builds the detail line.
An unknown event still renders, using the raw event name as its label, so adding a backend event
without a frontend branch degrades rather than breaks.

Two things are **load-bearing** and must not be "cleaned up":

- **`decision.query_type` is a ROUTE word (`task` / `general` / `realtime` / `mixed`), not the
  resolver's `kind`.** Three separate behaviours key off it: `addRouteClass()` for the row colour,
  the orb state transitions (`query_type === 'task'` → working), and the pre-search starter sound
  (`query_type === 'realtime'`). M13 changed the backend's routing vocabulary to
  `action` / `web_question` / `knowledge_question` / `visual` / `mixed`, so `chat_service._ROUTE_FOR_KIND`
  translates back to the old words for this field and carries the new one alongside as
  `decision.kind`. `tests/test_resolver_routing.py::TransparencyTests` locks that mapping.
- **`_actions._meta` must be acknowledged.** `handleActions()` POSTs `dispatch_id` + `action_id` to
  `/api/activity/frontend-ack`. That POST is now the **only** evidence any web action ever happened
  (§10) — if it is dropped, every `open_website` / `play_on_youtube` / `search_google` goes back to
  verdict UNKNOWN, never gets cached, and JARVIS starts hedging on actions that actually worked.
  Send it as early as possible: the Checker polls for it inside a short window.

M13 events, all `activity-sub` rows except `understood`: `understood` (the resolved goal, shown
every turn — §4.6 transparency), `verifying`, `verdict` (PASS green / else red), `retrying`,
`no_op_rejected`. `fast_path` was removed along with the regex fast path.

**Correction bubbles (M5):** `startBackgroundActivityPolling` checks `row.verdict === 'FAIL'` every 2s. FAIL rows also get a human-readable `message` from the backend. `addCorrectionMessage()` renders a distinct "Jarvis (correction)" bubble in the chat thread. PASS/UNKNOWN are side-panel only.

### History Drawer (`index.html` + `style.css` + `script.js`, M11)

Left-side drawer opened by the clock icon in the header (`#history-toggle`). Contains New conversation, a debounced search box, and the conversation list grouped **Today / Yesterday / Previous 7 Days / Previous 30 Days / Older**, plus loading-skeleton, empty, no-results, and error states and a Load-more button when the API returns a cursor. Each row has an ellipsis menu with Rename and Delete; both open accessible dialogs, and the delete dialog names the exact conversation and states that deletion cannot be undone.

All state lives in `historyState`. Key functions: `loadHistory()` (paginate/search), `renderHistory()` / `buildHistoryItem()` (text-only rendering), `selectConversation()`, `setActiveSession()`, `submitRename()`, `submitDelete()`, `restoreLastSession()`, `historyGroupFor()`.

Things that are easy to break here:
- **`historyGroupFor()` groups by calendar day, not elapsed hours.** 8pm yesterday read at 9am is "Yesterday" even though it is 13 hours ago. Diffing raw timestamps put every conversation in "Today".
- **`.history-item.menu-open { z-index: 5 }` is load-bearing.** Rows are `position: relative`, so without it a later sibling row paints over the open menu and a click on Rename lands on the next conversation. `.history-item-menu.up` flips the menu upward when it would be clipped by the list's `overflow-y`.
- **`.history-panel [hidden]` needs `display: none !important`.** Several rules set `display`, which otherwise beats the UA `[hidden]` rule and leaves dialogs permanently visible.
- **The overlay is mobile-only.** On desktop the drawer sits beside the chat and the composer stays usable — matching `.panel-overlay`, which is deliberately transparent and `pointer-events: none`.
- **`historyState.switching` is reset unconditionally**, not gated on `requestToken`. A search firing mid-switch bumps the token, and gating left the flag stuck `true`, blocking every later switch.
- Switching is refused while `isStreaming`, and reopening a chat calls `resetTurnPanels()` rather than rebuilding the Activity panel.

### URL Routing (`web/script.js` + `web/index.html` + `app/api/dashboard.py`, M12)

The URL is the **single source of truth** for which conversation is open — `localStorage` no longer plays any part in it.

| URL | Meaning |
|---|---|
| `/jarvis/` | A fresh chat with no id yet |
| `/jarvis/c/<session_id>` | That conversation |

A new chat has no id until the first reply comes back; the SSE `session_id` then triggers `setActiveSession(id)`, which **replaces** the history entry rather than pushing one — so Back does not land on an empty draft. Same behaviour as ChatGPT.

Key pieces: `CHAT_BASE_PATH` / `CHAT_URL_PREFIX` (derived from `location.pathname`, since the app is mounted at both `/jarvis` and `/app`), `sessionIdFromUrl()`, `syncUrl(id, mode)`, `openSessionFromUrl()` (initial load), `onHistoryPopState()` (Back/Forward).

`setActiveSession(id, urlMode)` and `selectConversation(id, {urlMode})` take a mode: `'replace'` (a draft becoming real), `'push'` (user opened another conversation), `'none'` (we got here *from* the URL — never write history back, or Back/Forward loops).

Things that are easy to break here:
- **`<base href="/jarvis/">` in `index.html` is load-bearing.** At `/jarvis/c/<id>` the relative `style.css` / `script.js` would otherwise be requested from `/jarvis/c/` and 404, leaving an unstyled dead page.
- **The deep-link route must stay registered before the static mounts** (`main.py` includes routers first). Move it after and every hard refresh 404s.
- **A dead id in the address bar re-404s on every refresh**, so `selectConversation` calls `syncUrl(null, 'replace')` when the id came from the URL.
- **Back/Forward is refused while `isStreaming`**, and the address bar is put back with `syncUrl(sessionId, 'replace')` so the URL never disagrees with what is on screen.
- Two tabs now hold two different conversations, which is what removed the shared-session hazard of the old `localStorage` restore.

### Orb System (`orb.js` + `script.js`)

**WebGL orb** renders via GLSL shaders with simplex noise. States (`idle`, `listening`, `thinking`, `searching`, `working`, `speaking`) control animation parameters (speed, noise, glow, wave, orbit, rotation, hue). `setProperty(key, value)` updates a single property instantly (used by dashboard sliders). `setStateInstant(name)` jumps to a state without lerp transition. `applyGlobals(config)` applies saved orb configuration from localStorage.

**Orb Dashboard** (`#orb-dashboard`): Full customization panel accessed via the gear-sun icon in the header. Controls: Speed, Noise, Glow Intensity, Wave, Orbit, Rotation, Hue, Glow Color, Glow Size, Pulse Period. Preset tabs (Idle/Listening/Thinking/Searching/Working/Speaking) use `setStateInstant()`. Sliders call `setProperty()` for real-time preview. Reset button restores defaults. Config persists to `localStorage['orbGlobals']`.

**Status Badge** (`#status-badge`): Dynamically updates text and dot color based on current orb state via `updateStatusBadge(stateName)`. States map to labels: idle→Online, listening→Listening, thinking→Thinking, etc. Dot color and badge background tint match each state's theme color (cyan for listening, purple for thinking, teal for searching, amber for working).

### UI Polish Features

- **Ambient Light:** `#orb-container::before` — large radial gradient behind the orb that changes color per state via `--ambient-color` CSS custom property
- **Star Field Background:** `body::before/::after` — subtle animated CSS radial-gradient particles that drift slowly, creating spatial depth
- **Staggered Welcome Entrance:** Welcome icon, title, subtitle, and each chip fade in with increasing delays via `fadeInUp` keyframes
- **Scroll-to-Bottom FAB:** `#scroll-fab` — floating button appears when scrolled >200px from bottom of chat, smooth-scrolls to latest message
- **Send Button Glow:** `.send-btn.has-text` — pulsing glow animation when input has text
- **Header Glassmorphism:** `backdrop-filter: blur(48px)` + gradient bottom border line
- **Chat Bubble Accents:** Assistant messages have a 2px purple left border; both message types have hover elevation
- **Button Micro-animations:** `btn-icon` elements scale up and lift on hover with spring-like cubic-bezier easing

---

## 14. Critical Design Rules

1. **Tools are the only truth.** Never claim an action happened without a tool call. A tool returning without `ERROR` = transport OK, **not** verified success.
2. **UNKNOWN ≠ PASS.** UNKNOWN never promotes to cache, never becomes a trusted habit. Never convert it because the tool returned friendly text.
3. **Dangerous tools require scoped confirmation** — bound to exact `action_id` + `tool` + `args_hash` + expiry. Confirming one risky step does **not** authorize later ones.
4. **One execution authority.** `ExecutionCoordinator` owns the tool lifecycle. Never add a second `registry.execute()` / `record_action()` / `publish_action_done()` call path.
5. **Never race providers during tool-calling.** Failover is deterministic and sequential.
6. **Fail-soft everything.** Any phase failing (watcher, memory, cache, checker, planner, proactive, user model, memory extractor, file index, maintenance) must leave chat working.
7. **Cache semantic matching retrieves but never auto-executes** (`CACHE_SEMANTIC_AUTO_EXECUTE=False`) until an adversarially-tested equivalence gate exists.
8. **Proactive = suggestions only** (`PROACTIVE_AUTO_ACT=False`). Never auto-execute dangerous/destructive/privacy/communication actions.
9. **Context registries are thread-local.** Concurrent sessions must not leak state.
10. **No hardcoded command lists — and no hardcoded language, anywhere.** Integrate via `@tool()` metadata, schemas, and capabilities — never `if command == "..."`, never app-name or Hindi/English phrase branches to make a test pass. **M13 deleted every remaining offender** (`try_direct_web_command`, `_OPEN_VERBS`/`_PLAY_VERBS`/`_SEARCH_VERBS`, `_SITE_MAP`, `_APP_ALIASES`, `_CLOSE_PRONOUNS`, `_is_affirmative`, `_is_negative`, `_is_retry_complaint`, `_needs_screen_look`, `_rule_based_primary`) and `tests/test_no_hardcoded_language.py` fails CI if any of them returns, by name **or** by shape (≥3 natural-language strings in a collection inside the routing files). What is deliberately KEPT: `_SETTINGS_URIS` (`ms-settings:` links are Windows' own addresses, not a guess), `detect_reference()` in the context engine (one owner, used to decide "ask", never to decide a route), and the tool-name sets in `learner.py`.
   The corollary the owner explicitly accepted: **no LLM ⇒ no routing.** Verified cache entries still replay offline; everything else becomes an honest error. A phrase list that half-works is worse than admitting the reasoning engine is unreachable.
11. **Never add a second watcher poller.** `_emit_events()` already diffs snapshots; a second one duplicates events and inflates habit counts. New data (clipboard ring, file index refresh) must hook the existing tick.
12. **Don't log secrets.** No API keys, tokens, raw clipboard, full screenshots, or unrestricted email/calendar content.
13. **Replay whole plans, in order.** Never replay only step 1 of a cached multi-step plan.
14. **Learning must be idempotent.** Restarting must not inflate habit counts (guarded by `test_user_model_idempotency.py`).
15. **`verification=` is mandatory on every tool.** `test_verifier_coverage.py` enforces this. New tools without a declared family will break CI.
16. **`cacheable=False` on all time-varying query tools.** `battery_status`, `get_datetime`, `network_info`, `system_resources`, etc. must never have their output replayed as a cached response.
17. **An action turn that executed nothing must never report success (M13).** The gate is control flow in `agent_loop.run_stream` (`expect_action` + `_NO_OP_NUDGE`), **not** a line in the system prompt — a model that ignores prose cannot ignore a branch. The reply on that path is never `"Done."`.
18. **The reply must not out-run the verdict (M13).** `_settle_before_reply` waits up to `VERIFY_WAIT_TIMEOUT` for the actions this turn ran. A timeout is UNKNOWN, and UNKNOWN is admitted, never retried and never upgraded. Wording is generated by the agent from the verdict, not templated.
19. **Only self-contained commands may be cached (M13).** Eligibility comes from the resolver and is recorded *before* execution. An unrecorded command is never promoted.
20. **Register a frontend dispatch inside `execute_action`, before `_actions` is yielded (M13).** Any other ordering re-creates the race where the browser ACKs a dispatch nobody recorded, which made every web tool permanently UNKNOWN. And drain the sink **per action** — never per run.

---

## 15. Databases (`data/`)

| DB | Tables | Purpose |
|---|---|---|
| `memory.db` | facts, actions, corrections + FTS5 | Persistent memory. WAL mode. Has `.pre-v2.bak` backup. |
| `skills.db` | skills, observations | Verified reusable workflows (min 3 repeats) |
| `command_cache.db` | entries (trigger, kind, payload, verification, hits, failures) | Verified command replay, max 500, LRU |
| `proactive.db` | suggestions, consent | Suggestion state + per-class consent |
| `user_model.db` | facts, aliases, habits | Personalization (min 3 observations) |
| `file_index.db` | file index | Fast `find_file` search over user folders (M7) |
| `reminders.db` | reminders | heapq scheduler persistence (M8) |
| `notes.db` | notes, todo_lists, todo_items | Notes + To-Do (M8) |

**Eight databases, not six.** Only five have a `*_DB_PATH` constant in `config.py`
(`MEMORY`, `SKILLS`, `COMMAND_CACHE`, `USER_MODEL`, `PROACTIVE`); `file_index.db`, `reminders.db`
and `notes.db` are built from `BASE_DIR / "data"` inside their own services. Anything that walks
"all the databases" (maintenance, backup, `verdict_report.py`) must not derive the list from
`config` alone.

All DBs opened via `app/services/db.py:open_db()` (M1) which sets WAL mode, autocheckpoint (1000 pages), and busy timeout (5000ms). `checkpoint_and_close_all()` is called on clean shutdown — after a proper shutdown there should be **no** `-wal` or `-shm` sidecar files. If you see a 2MB `memory.db-wal` with the server stopped, it means the server was killed, not shut down cleanly.

**The shutdown hook only runs on a graceful stop.** `taskkill /F`, closing the terminal, or killing a `run.py` that was launched without its own console group all skip the lifespan shutdown, and the sidecars are left behind. That is not a bug in `db.py` — `test_db_lifecycle.py` guards the hook itself. Use `start.bat` + Ctrl+C, or `scripts/_m13_graceful_stop.py`, and re-check with `scripts/verdict_report.py`. Verified clean on 2026-08-01: all 8 databases checkpointed, 0 sidecars.

Cache `kind` values: `KIND_TOOL` · `KIND_PLAN` · `KIND_RESPONSE`. Consent values: `CONSENT_ASK` · `CONSENT_ALLOW` · `CONSENT_DENY`.

Backups: `data/backups/YYYY-MM-DD/` — daily SQLite `.backup()` snapshots, kept 7 days (M2).

---

## 16. Testing

```bash
# Static — always run these two before claiming done
python -m compileall -q app tests scripts run.py config.py
node --check web/script.js && node --check web/orb.js && node --check web/api-monitor.js

# Unit / integration (347 passing in 25 files as of 2026-08-01; 215 before M13)
python -m pytest tests/ -v
python -m unittest -v tests.test_command_cache_execution

# Metrics / health
python scripts/verdict_report.py                    # verifier coverage, verdicts, WAL, latency
python scripts/verdict_report.py --write-baseline   # refreshes docs/BASELINE.md
python scripts/_m13_doc_audit.py                    # the real numbers this file claims — run it before editing §1/§3/§5/§6/§13/§16

# Live (needs running server / real desktop)
python scripts/smoke_test.py
python scripts/uia_probe.py status
python scripts/cache_inspect.py
python scripts/view_debug_log.py

# M13 one-off diagnostics (real LLM calls; the last two need a running server)
python scripts/_m13_resolver_check.py        # replays session 94bf07c2 through the real resolver
python scripts/_m13_provider_probe.py        # measures the real agent payload per provider
python scripts/_m13_live_turn.py             # 3 non-destructive turns end to end
python scripts/_m13_frontend_ack_check.py    # acts as the browser: proves ACK -> PASS -> promotion
python scripts/_m13_honesty_check.py         # plays the browser badly: UNKNOWN / FAIL+retry / state guard
python scripts/_m13_graceful_stop.py         # stops the server like Ctrl+C, then reports WAL sidecars
```

`_m13_honesty_check.py` is the one to run after touching anything in the verification
path. It withholds or rejects the browser ACK on purpose and asserts that (A) no ACK ⇒
UNKNOWN ⇒ the reply admits it, (B) a rejected ACK ⇒ FAIL ⇒ **exactly one** retry ⇒ an
honest admission naming the cause, (C) a follow-up complaint after an unverified action
reaches the agent instead of chat. All three verified passing on 2026-08-01.

| Test file | Lines | Guards |
|---|---|---|
| `test_command_cache_execution.py` | 167 | Promotion, verdict joining, eviction |
| `test_execution_models.py` | 102 | Typed contract validation |
| `test_command_signature.py` | 84 | Paraphrase matching; polarity (on≠off), Hindi/English |
| `test_execution_coordinator.py` | 70 | Atomic execution, plan failure, confirmation |
| `test_plan_shortfall.py` | 60 | Plans that only open but don't act are rejected |
| `test_execution_safety.py` | 58 | Frontend dispatch, confirmation scope, thread isolation |
| `test_user_model_idempotency.py` | 45 | Restart doesn't inflate habits |
| `test_db_lifecycle.py` | 112 | WAL truncated + connections closed on shutdown (M1) |
| `test_retention.py` | 205 | Prune rules, backup integrity (M2) |
| `test_memory_extractor.py` | 176 | JSON parsing, secret rejection, dedupe, per-turn cap (M3) |
| `test_verifier_coverage.py` | 88 | **Every** registered tool resolves to a family — regression gate (M4) |
| `test_new_verifiers.py` | 166 | frontend/memory/google/query verifier behavior (M4) |
| `test_failure_reporting.py` | 86 | Correction bubble delivery on FAIL (M5) |
| `test_tool_result_offload.py` | 104 | Large results offloaded to disk (M6) |
| `test_path_guard.py` | 137 | System paths blocked, user paths allowed (M7) |
| `test_conversation_history.py` | 389 | Title derivation, legacy-file metadata, rename persistence, atomic save, pagination, malformed-file skip, delete state cleanup (M11) |
| `test_conversation_history_api.py` | 209 | List/get/rename/delete route contracts, 400/404/422, error payloads leak no paths (M11) |
| `test_conversation_history.py::StartupBriefIsNeverPersistedTests` | — | The daily startup brief writes no chat file and never lists (M11 fix) |
| `test_conversation_history_api.py::ConversationDeepLinkTests` | — | `/jarvis/c/<id>` serves the shell, keeps `<base href>`, ignores the id as a path (M12) |
| `test_frontend_ack.py` | 193 | **M13 §3.1.** The whole ACK chain with a real `Phase4Coordinator`: `attach_dispatch` → `register_dispatch` → `acknowledge_dispatch` → `dispatch_result` → `_verify_frontend` returns **PASS**. Plus the stale-dispatch sweep and `wait_for_verdict` (hit, late arrival, timeout). |
| `test_action_sink_isolation.py` | 166 | **M13 §3.2.** Two web tools in one run ⇒ disjoint payloads, distinct `dispatch_id`s, both acknowledgeable, no URL emitted twice, sink empty after the coordinator drains it. |
| `test_agent_no_op_guard.py` | 256 | **M13 §3.3.** Fake LLM returning text-only on an action turn ⇒ nudge issued and actually reaching the model; still text-only ⇒ honest failure, reply is **not** `"Done."`. Only one nudge is spent. Conversational turns untouched. Empty completions are provider failures. Plus `ComposedWordingTests`: a truncated admission (`"I attempted"`) is rejected for the plain wording, because a fragment reads as a success claim. |
| `test_verify_before_reply.py` | 211 | **M13 §3.4.** PASS ⇒ confident reply; FAIL ⇒ exactly one retry carrying the reason; FAIL twice / UNKNOWN / timeout ⇒ the reply states what could not be confirmed; unsafe tools never auto-retried; the wait budget is per turn; the flag restores old behaviour. |
| `test_resolver.py` | 356 | **M13 §4.7.** Pronouns, ellipsis, Hinglish, complaints no phrase list contained, `unresolved` ⇒ ask (acting kinds only), JSON fences / `<think>` blocks / prose tolerated, one re-ask then safe fallback, capped key cycling, honest offline. |
| `test_resolver_routing.py` | 452 | **M13 §4.7.** Replays session `94bf07c2` turn by turn: turn 3 reaches the agent, turn 6 never receives `"it"`, the whole session never reaches conversation mode. Plus the state-based guard, kind→route mapping, camera vs screen, clarification, offline, confirmation grant/cancel/ask, transparency events, the `decision.query_type` route-vocabulary contract, mixed-turn verification, and cache-eligibility wiring. |
| `test_no_hardcoded_language.py` | 195 | **Regression gate for Rule #10.** Every deleted symbol stays deleted (AST-checked), `brain_service.py` stays gone, no collection of ≥3 natural-language phrases appears in the routing files, and the kept factual data (`_SETTINGS_URIS`, single `detect_reference`) is still there. |
| `test_cache_self_contained.py` | 187 | **M13 §4.4.** Context-dependent commands never promote even after repeated PASS; unrecorded commands never promote; both promotion paths gated; eligibility map bounded; FAIL still evicts. |

**Never** use real Wi-Fi toggling, shutdown, email sending, file deletion, or browser side-effects in automated tests. Use fake registries, fake watcher state, fake clocks, temp SQLite.

**Patch `chat_service.CHATS_DATA_DIR` to a temp directory in any test that drives a turn.** A routing test that streams a turn will otherwise write a real conversation file into `data/chats_data/` and it will show up in the owner's history sidebar. `tests/test_resolver_routing.py::RoutingTestCase` does this and tears it down; copy that pattern.

---

## 17. Running

```bash
start.bat          # Recommended: frees port 8000, starts, waits for /health, opens UI
python run.py      # Manual
```
Main UI `http://localhost:8000/jarvis/` · Control Center `/dashboard` · Watcher `/watcher` · Health `/health`

---

## 18. Known Issues

1. `run.py` binds `0.0.0.0` — should be `127.0.0.1` (user explicitly declined to fix for personal use)
2. CORS allows all origins (`main.py` L93-99) — needs restriction (user declined)
3. No API authentication (user declined — single-user local app)
4. `web/style.css` has rule duplication (4470 lines) — some append-only overrides from iterative UI upgrades remain
5. `viewer.html` calls `/tasks/{task_id}` — **backend route does not exist**
6. Google OAuth `invalid_grant` when token expires — delete `data/google_token.json` and re-auth. **Also required after M7** because `gmail.send` scope was added — any token from before M7 will not have send permission.
7. `README.md` / `implementation_plan.md` describe stale directory names (see §4)
8. Broad dependency ranges in `requirements.txt` — no lockfile
9. `command_cache` growth was blocked by the dead ACK chain, not by the promotion logic — **fixed in M13**. Web tools now reach PASS and promote (verified live: `open example.com in the browser` promoted, then replayed as a cache hit on the next turn). It still needs repeated real use to grow.
10. UNKNOWN verdict share was ~38% historically. The M13 fix removes the single largest structural cause (every `family="frontend"` action was permanently UNKNOWN), but the numbers in `memory.db` are dominated by ~60 pre-M13 rows, so **a fresh baseline is only meaningful after a session's worth of real use.** Re-measure with `scripts/verdict_report.py` then. Some UNKNOWN is legitimate — the 10 `family="none"` tools are unverifiable by design.
11. **The Groq agent fallback cannot run on the current tier.** Measured 2026-08-01: the agent request is ~8.1 k tokens against a `Limit 8000` TPM cap, so every key returns HTTP 413. The payload is dominated by the 95 tool schemas (~42 KB). Until the tier is raised, **Gemini is the only usable agent provider** — if Gemini is down, action turns fail honestly rather than falling back. Options if this matters: raise the Groq tier, or reduce the tool-schema payload (e.g. shorter descriptions, or exposing a subset per turn — the latter must not become a hardcoded route table, see Rule #10).
12. Resolver latency is usually 2–4 s but spiked to 11–15 s on cold/uncached turns during live testing. It replaces a ~600 ms classifier, so an action turn can be a second or two slower than before; `RESOLVER_MAX_FAILOVER_KEYS=3` bounds the outage case.
13. M9 (user SKILL.md skills), M10 (MCP client) are NOT implemented

### Future Roadmap
- **M8 Improvements**: Voiced reminder notifications — when a reminder fires, JARVIS speaks
  the reminder aloud (e.g. "Sir, mummy ko call karne ka time ho gaya hai", "Exercise ka time
  hai sir") instead of just showing a popup/notification. Requires wiring the TTS pipeline
  to the SSE notification callback so the orb speaks proactively.
- **Telegram / WhatsApp integration**: Push reminder notifications and quick-reply to phones
- **Spotify control**: Play/pause/skip/search via voice commands
- **Hindi voice support**: Regional TTS voice for Hindi commands/responses
- **M9**: User SKILL.md skills — user-defined multi-step workflows
- **M10**: MCP client — external tool servers via Model Context Protocol
- **Quick Info Dashboard**: Glanceable widget showing weather, next event, unread emails
- **History follow-ups (deliberately deferred from M11)**: bulk select/delete, archive/folders/tags,
  pinning, export to Markdown/JSON, LLM-generated titles, SQLite FTS index for very large
  histories, cross-device sync, search-term highlighting inside an opened transcript

**Issue 4 from pre-M7 list is partially resolved:** `_guard_path()` now blocks writes/deletes under Windows system dirs and the JARVIS repo itself. Read and open operations on files outside user dirs are still unrestricted.

---

## 19. Decision Log

*Reasoning that is NOT derivable from the code or git history.*

| Decision | Why |
|---|---|
| Gemini primary over Groq for the agent | Groq/gpt-oss emits malformed tool calls → HTTP 400. Gemini's tool-calling is reliable. Groq stays as fallback because it's fast and cheap. |
| Fail over to Gemini immediately on a Groq 400 | The failure is per-model, not per-key — retrying every Groq key wastes seconds for a guaranteed identical failure. |
| Z.ai/GLM disabled, not deleted | Kept failing in production. Clients array emptied so it can be re-enabled without rewriting the integration. |
| Never race providers during tool-calling | Two providers racing can both execute the same tool. Determinism beats latency here. |
| Separate STA thread for audio vs UI | A hung pywinauto call would otherwise block volume changes. Two apartments = independent failure domains. |
| UI apartment retires after 8 generations | pywinauto/comtypes leak COM references; periodic retirement avoids a slow degradation that looked like random UI failures. |
| pywinauto UIA over raw Win32 | Higher-level control discovery; handles COM apartments. |
| `_find_app_shortcut` scans the Start Menu | Apps in AppData (Telegram, Discord, Spotify) aren't on PATH, so `start <name>` fails. The `.lnk` is the no-hardcode path to launch them exactly as the user would. |
| Watcher snapshots PIDs before launch | The only reliable way to make "close it" work for unknown and UWP apps without a hardcoded exe map. |
| `plan_shortfall` rejects open-only plans | The planner kept "succeeding" by opening Settings without changing the setting. Opening ≠ doing. |
| Semantic cache retrieves but can't auto-execute | Embedding similarity does not preserve polarity or entities — "wifi on"/"wifi off" are near-identical vectors. Needs a structured equivalence gate first. |
| SQLite over Postgres, 8 DBs not 1 | Single-user local app; per-phase DBs give independent lifecycle, backup, and migration. `file_index.db` added in M7; `reminders.db` + `notes.db` in M8. |
| No frontend framework | A single chat page doesn't justify React/Vue; vanilla JS + SSE is enough. |
| Edge TTS over local TTS | Good voices with no GPU; accepted cost is an internet dependency. |
| Thread-local action sink | Prevents concurrent requests from mixing frontend actions. |
| Vector store is for docs/skills, not code navigation | For code, exact-match grep beats embeddings; semantic search earns its keep only on unstructured text. |
| `open_db()` shared helper (M1) | Every phase DB opened connections independently with no WAL setup and no close/checkpoint on shutdown. Centralizing fixed the 2MB WAL sidecar and guarantees clean shutdown — provided the process is stopped gracefully (§15). |
| Background LLM extraction for memory (M3) | `auto_capture()` regex catches obvious phrases but missed most natural conversation. An LLM pass after each turn reliably extracts facts — fire-and-forget thread means zero chat latency impact. |
| Size-based tool-result offloading, not tool-list (M6) | A tool-name whitelist would be a hardcoded command list (violates Rule #10). Size is objective and catches any tool that returns unexpectedly large output. |
| `_guard_path()` as accident guard not security (M7) | The LLM can hallucinate paths. Blocking system directories prevents accidental destructive operations without claiming to be a security boundary — security was user-declined. |
| File index via own SQLite, not Windows Search (M7) | Windows Search indexing may be disabled or slow; own SQLite gives controlled scope, fast queries, and no OS dependency. |
| winsdk OCR for `read_screen` (M7) | Already a dependency for radio control. Zero new install, offline, good quality on Windows 10+. |
| Chat JSON stays the source of truth for history (M11) | 44 conversations is a directory scan, not a database problem. Metadata is derived on read for legacy files, so there is no migration step. The API response shape is independent of the disk format, so a SQLite metadata index can be added later without touching the frontend. |
| Conversation delete is permanent, no trash folder (M11) | User's explicit choice over the recommended soft-delete. The UI confirmation naming the exact conversation is therefore the only safety net, and `data/backups/` does **not** cover chat JSON — it snapshots the SQLite DBs only. |
| Reopening a chat does not restore the Activity panel (M11) | Chat files persist only user/assistant messages. Reconstructing execution telemetry from them would mean presenting incomplete or unrelated verdicts as if they belonged to the current turn. |
| History drawer has no dimming overlay on desktop (M11) | The existing `.panel-overlay` is deliberately transparent and `pointer-events: none` so a side panel never blocks the composer. The dimming overlay is mobile-only to match. |
| URL is the source of truth for the open conversation, not `localStorage` (M12) | Two tabs sharing one `localStorage` key meant two tabs streaming into one session id — and all 5 streaming paths accumulate into `sessions[sid][-1]`, so the second tab's user message would have absorbed the first tab's reply. Per-tab URLs remove the shared pointer entirely. |
| The id appears only after the first reply, via `replaceState` (M12) | Matches ChatGPT. Pushing an entry for the empty draft would make Back land on a blank chat; creating an id before the first message would litter the sidebar with empty conversations. |
| Deep links serve the shell for **any** id, even unknown ones (M12) | The server does not know whether the frontend considers a conversation gone. Returning 404 for an unknown id would break Back/Forward navigation into a deleted chat; the frontend shows a toast and rewrites the URL instead. |
| No `run_shell_command` tool | Would bypass the entire tool-metadata, verification, confirmation, and path-guard model. Any capability it provides can be covered by targeted tools. |
| **Understanding replaces classification, rather than being added to it (M13)** | The resolver costs one LLM call — the same call `BrainService.classify_primary` already made. Adding a layer would have been a latency regression; substituting one is free, and a self-contained goal is strictly more useful than a category word. |
| **Delete every language list, accepting that no LLM ⇒ no routing (M13)** | Owner's explicit requirement. Five independent phrase lists each failed silently on the first phrasing nobody anticipated: "It's not playing" was missing from the retry-complaint list, so a failed action was answered with small talk. A list that half-works hides the failure; an honest "I can't reach my reasoning engine" does not. Verified cache entries still replay with no LLM, so the offline path is not empty. |
| **`_SETTINGS_URIS` kept while `_SITE_MAP` and `_APP_ALIASES` were deleted (M13)** | `ms-settings:display` is Windows' own address for a thing, like a phone number. `"x" → twitter.com` and `"paint" → mspaint` are guesses about what a *word means*. Removing an address makes JARVIS worse without making it smarter; removing a guess makes it smarter. App launching now asks the machine (`shutil.which`, then the Start Menu) instead of consulting a table. |
| **`visual_source` added to the plan's JSON contract (M13)** | The plan collapsed camera and screen into one `visual` kind, but they need different machinery — a browser capture vs the screen-reading tools. The only alternatives were a keyword list (the thing being deleted) or losing the camera route. Letting the model say which surface it means is neither. |
| **`unresolved` blocks acting turns only (M13)** | Guessing is dangerous when something will change — the wrong recipient, the wrong file. It is merely unhelpful before a search. Live testing showed the model flagging "who won the match last night" as needing a sport; interrogating the owner there is worse than searching. |
| **The no-op guard is control flow, not a prompt instruction (M13)** | The system prompt already said "Do not pretend an action happened." The model returned `"Done."` with zero tool calls anyway (session 94bf07c2, turn 2). A branch that refuses to accept the answer cannot be ignored; a paragraph can. |
| **An empty completion is a provider failure, not an answer (M13)** | `gemini-2.5-flash` draws thinking tokens from the output budget, so a long prompt plus 95 tool schemas could return `content=''` with no tool calls. Treating that as the model's final answer is indistinguishable from the model deciding to do nothing — the exact lie this milestone exists to remove. It fails over instead, and `AGENT_REASONING_EFFORT=none` removes the cause. |
| **Wait for the verdict before replying, cap it at 3s, treat a timeout as UNKNOWN (M13)** | Verification runs on the event bus, so the verdict landed ~4s *after* the reply had streamed and the only possible correction was a late bubble — `_failure_message()` exists precisely because JARVIS "ran out of turn". Owner's choice: retry once, then admit. A lie costs more than 2s of honesty. A timeout must never be optimistic, because the whole point is that silence is not evidence. |
| **Retry safety is decided from tool metadata, not a name list (M13)** | `retry_is_safe()` reads `dangerous` + `verification.family`, so a tool registered tomorrow is classified correctly without anyone remembering to add it to a set. `input`/`file`/`ui` are excluded because typing or saving twice is a real duplicate effect and a FAIL may be false. |
| **Cache eligibility is recorded before execution, and defaults to ineligible (M13)** | The verdict that promotes an entry arrives before the reply is composed, so the flag has to be in place first. Defaulting an unrecorded command to *ineligible* means a new code path that forgets to declare eligibility loses speed, not correctness — the safe direction. |
| **A cache replay is verified like any other execution (M13)** | Otherwise the fast path is the one path that can lie, and a stale entry would keep reporting success forever. The existing self-healing eviction depends on it. |
| **The resolved goal is shown in the Activity panel every turn (M13)** | The one new failure mode this milestone introduces is a confidently-wrong resolution. Showing what was understood turns that from a mysterious wrong outcome into a visible misunderstanding the owner can correct immediately. It is the mitigation, not decoration. |
