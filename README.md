# J.A.R.V.I.S

A local, voice-enabled AI assistant for Windows that can chat, search the web, understand live desktop context, control applications, work with files, use Google services, remember user preferences, plan multi-step tasks, verify actions, and learn from repeated workflows.

This README is the single source of project documentation. It combines the earlier master plan, phase notes, testing guides, watcher roadmap, and automation notes into one organized reference.

> **Current state:** Advanced local prototype. Most planned intelligence layers are implemented, but full real-machine regression testing and security hardening are still required before production use.

---

## 1. What J.A.R.V.I.S Does

J.A.R.V.I.S supports three main types of work:

1. **Conversation**
   - General questions and answers
   - Voice input and spoken responses
   - Persistent chat sessions
   - User preferences and long-term memory

2. **Realtime information**
   - Web search through Serper
   - Search-query extraction through the configured LLM providers
   - Realtime answers using retrieved information

3. **Computer automation**
   - Open, close, focus, minimize, maximize, and interact with applications
   - Type text, press shortcuts, click, scroll, and use the clipboard
   - Read, write, create, move, open, trash, and delete files
   - Control volume, brightness, media, Wi-Fi, Bluetooth, power, and Windows settings
   - Use Gmail, Google Calendar, and Google Drive
   - Plan and execute multi-step tasks
   - Verify completed actions and learn reusable workflows

---

## 2. Core Design Principles

The project is built around these priorities:

- **Reliability first:** optional intelligence layers should fail softly instead of crashing chat or voice flows.
- **Local context:** system state, memory, habits, and learned skills are stored locally.
- **Fast daily use:** normal voice/chat requests should not wait for heavy background processing.
- **Visible execution:** J.A.R.V.I.S should only claim an action is complete after a tool runs or the result is visible.
- **Controlled automation:** destructive or sensitive actions should require confirmation.
- **Progressive intelligence:** watcher state and memory feed context resolution, planning, verification, proactive suggestions, and personalization.

---

## 3. Architecture

```text
Browser UI
   |
   v
FastAPI application (app/main.py)
   |
   +-- ChatService
   |      +-- General chat
   |      +-- Realtime chat/search
   |      +-- Voice/TTS flow
   |      +-- Agentic automation
   |
   +-- LLM provider layer
   |      +-- Groq
   |      +-- Gemini fallback/race paths
   |      +-- Optional Z.ai configuration
   |      +-- Key rotation, cooldown, and monitoring
   |
   +-- AgentLoop
   |      +-- Tool registry
   |      +-- Confirmation gate
   |      +-- Context registry
   |      +-- Action/event output
   |
   +-- Intelligence services
          +-- System Watcher
          +-- Memory Service
          +-- Context Engine
          +-- Planner and Executor
          +-- Checker and Learner
          +-- Skill Store
          +-- Command Cache
          +-- Proactive Engine
          +-- User Model
```

### Shared context model

All advanced behavior depends on two foundations:

- **Watcher = live state:** what applications, windows, settings, and processes exist right now.
- **Memory = historical state:** what happened before, what the user prefers, and what corrections were made.

The higher layers consume these foundations:

```text
Watcher + Memory
       |
       v
Context resolution
       |
       v
Multi-step planning and verification
       |
       v
Proactive suggestions and personalization
```

---

## 4. Project Structure

```text
jarvis-2-main/
├── app/
│   ├── main.py                         # FastAPI app, lifecycle, routes, streaming, TTS/STT
│   ├── models.py                       # Request/response models
│   ├── static/                         # Control Center and Watcher dashboards
│   ├── services/
│   │   ├── agent/
│   │   │   ├── agent_loop.py           # LLM tool-calling loop
│   │   │   ├── tool_registry.py        # Tool definitions and execution
│   │   │   ├── tools/                  # Desktop, files, web, Google, memory, system
│   │   │   ├── automation/             # Windows UI Automation engine
│   │   │   ├── planner/                # Multi-step planner and executor
│   │   │   ├── phase4/                 # Verification, learning, and skills
│   │   │   ├── phase6/                 # Verified command cache
│   │   │   ├── phase7/                 # Proactive suggestions
│   │   │   └── phase8/                 # User model and habit learning
│   │   ├── context/                     # Entity registry and reference resolver
│   │   ├── skills/                      # Gmail, Calendar, Drive, OAuth
│   │   ├── testing/                     # Real-machine command test runner
│   │   ├── watcher/                     # Live Windows state watcher
│   │   ├── brain_service.py             # Request classification/routing
│   │   ├── chat_service.py              # Main conversation orchestrator
│   │   ├── groq_service.py              # General LLM conversation service
│   │   ├── realtime_service.py          # Web-grounded responses
│   │   ├── memory_service.py            # Persistent memory and retrieval
│   │   ├── api_key_monitor.py           # Provider/key statistics
│   │   ├── vector_store.py              # Optional FAISS knowledge retrieval
│   │   └── vision_service.py            # Camera/image understanding
│   └── utils/                            # Retry, key rotation, and time helpers
├── web/                             # Main browser interface
├── data/                             # Local runtime state and caches
├── future jarvis/                        # Preserved future-product references
├── config.py                             # Environment and feature configuration
├── run.py                                # Python server launcher
├── start.bat                             # Windows one-click launcher
├── watcher_dashboard.py                  # Terminal watcher dashboard
├── _agent_test_harness.py                # Manual tool smoke-test harness
├── requirements.txt                      # Python dependencies
└── README.md                             # Complete project documentation
```

---

## 5. Unified Intelligence Phases

The earlier phase documents have been consolidated here. The phase names remain useful for understanding dependencies, but they are parts of one system rather than separate projects.

### Foundation — System Watcher

**Purpose:** Maintain a live local world model so automation does not depend on guesses.

**Implemented capabilities:**

- Open application and process tracking
- Real process IDs
- Active window and open-window state
- Recently launched application registry
- Clipboard preview
- Wi-Fi, Bluetooth, volume, and brightness state
- State-change events
- Fail-soft background refresh

**Main code:**

- `app/services/watcher/state_service.py`
- `app/services/watcher/__init__.py`
- `app/services/agent/phase7/events.py`

**Important behavior:** If watcher initialization or refresh fails, normal chat and tools should continue with reduced context.

### Memory — Long-term continuity

**Purpose:** Remember useful facts, preferences, corrections, and prior actions.

**Memory types:**

| Type | Storage | Purpose |
|---|---|---|
| Working memory | Current chat session | Recent conversation continuity |
| Profile memory | `data/memory/` | User profile and assistant persona |
| Fact memory | `memory.db` | Durable preferences and user facts |
| Action memory | `memory.db` | Recently executed tools and targets |
| Correction memory | `memory.db` | Prevent repeating corrected mistakes |
| Search index | SQLite FTS5 | Retrieve relevant memories by query |

**Learning paths:**

- Explicit requests such as “remember that…”
- Conservative regex-based auto-capture
- Automatic action logging
- Explicit correction recording

**Safety:**

- Password/API-key-like values should be rejected
- Stored values and action history are capped
- Duplicate fact keys update instead of multiplying
- Memory exceptions should not break chat

**Main code:**

- `app/services/memory_service.py`
- `app/services/agent/tools/memory_tools.py`

### Context Engine — Understanding “this”, “that”, and “the first one”

**Purpose:** Resolve ambiguous references against live state, conversation, memory, and tool results.

**Entity sources:**

- Watcher applications and windows
- Recent conversation mentions
- Recent tool results
- Clipboard and settings state
- Remembered actions and aliases

**Entity examples:**

- Application
- Window
- File
- URL
- Clipboard content
- Setting
- Tool result
- Person

**Resolution flow:**

1. Infer the expected entity type from the command.
2. Collect compatible entities.
3. Rank them using recency, focus, mention history, type match, and frequency.
4. Resolve automatically when confidence is high.
5. Ask a short clarification when candidates are too close.
6. Pass a concrete handle, path, PID, title, or URL to the tool.

**Main code:**

- `app/services/context/context_engine.py`
- `app/services/context/__init__.py`
- Context integration inside `agent_loop.py` and desktop tools

### Verification and Learning

**Purpose:** Check whether an executed action actually worked and learn from verified outcomes.

**Components:**

- Event bus for action-completion events
- Deterministic checker
- Optional vision verification
- Retry learner
- Repeated-workflow observation
- Verified skill storage

**Risk behavior:** Reversible actions can be retried conservatively. Irreversible actions should not be retried without confirmation.

**Main code:** `app/services/agent/phase4/`

### Multi-step Planner

**Purpose:** Convert a larger goal into small tool steps and verify each step.

**Flow:**

1. Build a structured plan.
2. Execute one step at a time.
3. Check preconditions and current state.
4. Pause before risky steps.
5. Verify the result.
6. Retry only when safe.
7. Stop with a clear result if the plan cannot continue.

**Main code:** `app/services/agent/planner/`

### Verified Command Cache

**Purpose:** Reuse previously successful commands instead of reasoning from zero every time.

The cache records normalized triggers, command payloads, verification status, hit count, failures, and timestamps.

**Main code:** `app/services/agent/phase6/`

### Proactive Engine

**Purpose:** Turn watcher events into permission-controlled suggestions.

Examples include repeated patterns or context changes that may justify a suggestion. Consent state and suggestion status are stored locally.

**Main code:** `app/services/agent/phase7/`

### User Model and Personalization

**Purpose:** Learn habits and preferences from repeated actions.

The user model stores facts, aliases, and recurring context/action pairs. This layer should improve ranking and suggestions without silently performing high-risk actions.

**Main code:** `app/services/agent/phase8/`

---

## 6. Agent Tool System

Tools are registered through `app/services/agent/tool_registry.py` and exposed to the agent as structured schemas.

### Desktop and window tools

- Open and close applications
- Focus, minimize, maximize, restore, and close windows
- Type text and press keyboard shortcuts
- Click and scroll
- Take screenshots
- Read/write clipboard
- List open windows

### File tools

- List directories
- Read and write text files
- Open files/folders
- Create folders
- Move or rename paths
- Move items to Recycle Bin
- Permanently delete files

### System tools

- Volume, mute, brightness, and media control
- Battery and system resources
- Process listing and termination
- Date/time and network information
- Wi-Fi and Bluetooth
- Windows Settings pages
- Lock, sleep, hibernate, sign out, shutdown, restart
- Camera control

### Web and content tools

- Open websites
- Google and YouTube search
- Play YouTube content
- Generate images
- Generate written content

### Google tools

- Gmail inbox and unread summaries
- Calendar list, search, create, and delete
- Drive search, list, and upload

### Memory and planning tools

- Remember, recall, forget, and record corrections
- Execute multi-step plans
- Interact through Windows UI Automation

---

## 7. LLM Providers and Routing

The application can use multiple providers and keys.

### Main paths

- **General chat:** Groq with fallback support
- **Realtime chat:** search-query extraction plus web-grounded response
- **Agent/tool calling:** deterministic provider failover; providers are not raced during tool execution
- **Brain classification:** optional Groq/Gemini race mode
- **Vision:** configured vision model path

### Reliability features

- Multiple keys per provider
- Key rotation
- Rate-limit detection
- Provider cooldown
- Primary/fallback order
- Streaming provider activity events
- Repetition-loop detection
- API-key health dashboard

Actual provider order is controlled by `config.py` and `.env`. Check runtime logs and the API monitor rather than relying on an old phase label.

---

## 8. Frontend and Dashboards

### Main UI

The main interface is available under `/jarvis/` and includes:

- Text and microphone input
- Streaming answers
- Spoken responses
- Thinking audio cues
- Provider/tool activity
- Conversation sessions
- Generated content and image actions

### Control Center

Open `/dashboard` to view:

- Watcher state
- Memory statistics
- Planner/checker status
- Learned skills and command cache
- Proactive engine state
- User-model information
- Command testing controls

### Watcher dashboard

Open `/watcher` for a focused live state view. A terminal dashboard is also available through `watcher_dashboard.py`.

### API-key monitor

Open `web/api-monitor.html` through the running application to inspect provider attempts, successes, failures, rate limits, cooldowns, and recent events.

---

## 9. API Overview

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Service health |
| POST | `/chat` | Standard chat |
| POST | `/chat/stream` | Standard streaming chat |
| POST | `/chat/realtime` | Realtime chat |
| POST | `/chat/realtime/stream` | Realtime streaming chat |
| POST | `/chat/jarvis/stream` | Unified Jarvis/agent stream |
| GET | `/api/startup-brief/stream` | Startup briefing |
| GET | `/chat/history/{session_id}` | Saved session history |
| POST | `/tts` | Text-to-speech |
| POST | `/transcribe` | Audio transcription |
| GET | `/api/key-monitor` | Provider/key statistics |
| GET | `/api/watcher/state` | Current watcher state |
| POST | `/api/test-session/run` | Start command-test session |
| GET | `/api/test-session/{session_id}/logs` | Test-session logs |
| GET | `/api/dashboard/state` | Consolidated control-center state |
| GET/POST | `/api/proactive/...` | Proactive suggestions and consent |
| GET/POST | `/api/usermodel/...` | User-model inspection and forgetting |
| GET | `/dashboard` | Control Center |
| GET | `/watcher` | Watcher dashboard |

> The generated-result viewer currently references `/tasks/{task_id}`. Verify or implement this backend route before depending on the viewer flow.

---

## 10. Installation

### Requirements

- Windows 10 or Windows 11
- Python 3.10+ recommended
- Microphone for voice input
- Internet for cloud LLM, search, Google APIs, and Edge TTS
- Optional webcam for vision features

### Create a virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### Configure environment

Create or update `.env` in the project root. Common settings include:

```dotenv
GROQ_API_KEY=your_key
GROQ_MODEL=your_model
GROQ_BRAIN_MODEL=your_brain_model

SERPER_API_KEY=your_key

GEMINI_API_KEY=your_key
GEMINI_MODEL=your_model
GEMINI_BRAIN_MODEL=your_brain_model
ENABLE_GEMINI_FALLBACK=True
ENABLE_RACE_MODE=False

TTS_VOICE=en-GB-RyanNeural
TTS_RATE=+15%
ASSISTANT_NAME=J.A.R.V.I.S
JARVIS_OWNER_NAME=Your Name
JARVIS_USER_LOCATION=Your Location
```

Additional numbered keys such as `GROQ_API_KEY_2` and `GEMINI_API_KEY_2` can be configured for rotation.

Never commit or share `.env`, OAuth tokens, or client secrets.

### Google integration

1. Create a Google OAuth desktop application.
2. Place its client configuration in the configured credentials file.
3. Start J.A.R.V.I.S and complete the browser consent flow.
4. The local token is stored under `data/`.

Configured scopes include Gmail read access, Calendar management, and Drive access. If logs show `invalid_grant`, remove/revoke the stale token and authenticate again.

---

## 11. Running J.A.R.V.I.S

### Recommended Windows launcher

Double-click:

```text
start.bat
```

The launcher:

1. Selects a local virtual environment when available.
2. Frees port `8000` from an old J.A.R.V.I.S process.
3. Starts the FastAPI server.
4. Waits for `/health`.
5. Opens the main UI and Control Center.

### Manual launch

```powershell
python run.py
```

Then open:

- Main UI: `http://localhost:8000/jarvis/`
- Control Center: `http://localhost:8000/dashboard`
- Watcher: `http://localhost:8000/watcher`
- Health check: `http://localhost:8000/health`

---

## 12. Local Storage

Runtime state is stored under `data/`.

| Path | Purpose |
|---|---|
| `memory.db` | Facts, actions, corrections, FTS memory index |
| `skills.db` | Verified skills and observations |
| `command_cache.db` | Reusable verified commands |
| `proactive.db` | Suggestions and consent state |
| `user_model.db` | Facts, aliases, and habits |
| `memory/` | User profile and assistant persona |
| `chats_data/` | Persisted chat sessions |
| `voice_cache/` | Generated TTS files |
| `camera_captures/` | Local captured images |
| `vector_store/` | Optional FAISS retrieval index |
| `google_token.json` | Local Google OAuth token |

SQLite may temporarily create `-wal` and `-shm` files while the application is running. These are runtime artifacts and should not be included in clean archives.

---

## 13. Testing Guide

Testing should happen in three levels.

### Level 1 — Static validation

```powershell
python -m compileall -q app config.py run.py watcher_dashboard.py _agent_test_harness.py
node --check frontend\script.js
node --check frontend\orb.js
node --check frontend\api-monitor.js
```

### Level 2 — Startup and health

1. Install dependencies.
2. Start the application.
3. Confirm `/health` responds.
4. Confirm the main UI and dashboards load.
5. Check that memory, watcher, planner, and provider services initialize without fatal errors.

### Level 3 — Real-machine functional matrix

#### Conversation and voice

- General text chat
- Voice transcription
- TTS playback
- Long and short replies
- Session history after restart

#### Provider reliability

- Groq primary success
- Secondary key rotation
- Gemini fallback
- Rate-limit cooldown
- Realtime query extraction
- Streaming response completion

#### Memory

- Remember a name or preference
- Recall it in a new turn
- Restart and recall again
- Record a correction
- Verify secret-like values are rejected
- Confirm profile-file instructions are injected

#### Context resolution

- Open one app, wait several turns, then say “close this”
- Open two apps and test ambiguity handling
- Search and ask to open “the first result”
- Create a file and refer to “that file”
- Stop the watcher and confirm chat remains usable

#### Desktop automation

- Open/close/focus applications
- Type and press shortcuts
- Window controls
- Clipboard
- Screenshot
- UI Automation controls

#### Files

- Create, read, overwrite, move, trash, and delete a test file
- Confirm destructive actions ask permission
- Test Desktop, Documents, and Downloads path resolution

#### System controls

- Volume, mute, brightness, and media
- Battery and resource status
- Wi-Fi/Bluetooth state
- Windows settings
- Confirmation for process termination and power actions

#### Google services

- Gmail inbox and unread count
- Calendar list/search/create/delete
- Drive search/list/upload
- OAuth expiry and token refresh

#### Multi-step and learning

- Execute a multi-step goal
- Confirm each step is not duplicated
- Verify risky-step pause
- Confirm checker output
- Repeat a workflow and inspect skill/cache updates

#### Stability

- Run for an extended session
- Check background watcher resource use
- Confirm provider failures do not crash the server
- Confirm memory/database errors fail softly
- Inspect the Control Center for stale or inconsistent state

### Manual test harness

`_agent_test_harness.py` contains direct tool smoke tests. Run only on a controlled Windows machine because some tools affect the real desktop and file system.

---

## 14. Security and Privacy

J.A.R.V.I.S has powerful local-machine access. Treat it like an administration tool.

### Required precautions

- Keep the service bound to localhost unless remote access is deliberately secured.
- Do not expose port `8000` to an untrusted network.
- Restrict CORS before any remote deployment.
- Add authentication before allowing non-local clients.
- Keep `.env`, OAuth credentials, tokens, databases, chats, screenshots, and logs private.
- Confirm exact tool arguments for destructive actions.
- Add trusted-directory boundaries before allowing autonomous file access.
- Avoid shell execution with untrusted input.
- Back up important files before automation testing.

### Current hardening gaps

- `run.py` currently binds to `0.0.0.0`.
- CORS currently permits all origins.
- Application-level API authentication is not implemented.
- File tools are not limited to a trusted workspace.
- Confirmation is primarily tool-name based rather than bound to an exact argument payload.

These items should be fixed before production or network use.

---

## 15. Known Issues and Limitations

1. The generated-result viewer calls `/tasks/{task_id}`, but the corresponding backend route is not currently defined.
2. `web/audio/starter_2.mp3` is empty and should be regenerated.
3. Google OAuth may report `invalid_grant` when the saved refresh token is expired or revoked.
4. Most testing is manual; a complete automated unit/integration suite is not currently present.
5. Several central files are large and should eventually be split into smaller modules.
6. Windows automation depends on the local desktop session and cannot be fully verified in a headless environment.
7. Broad dependency ranges can produce version drift; a tested lockfile is recommended.
8. Some historical comments describe provider orders that have since changed. Runtime configuration and current code are authoritative.

---

## 16. Development Guidelines

When extending the project:

1. Keep optional systems fail-soft.
2. Do not add new automation without a clear risk level.
3. Mark destructive tools as dangerous.
4. Bind confirmation to the exact action whenever possible.
5. Add a test for every bug fix.
6. Keep UI activity messages aligned with actual execution.
7. Never log secrets, clipboard contents, or complete OAuth tokens.
8. Store persistent state under `data/`, not inside source folders.
9. Avoid adding another phase document; update this README instead.
10. Preserve the `future jarvis/` folder as the product-reference archive.

---

## 17. Recommended Next Steps

### Priority 1 — Security

- Bind to `127.0.0.1` by default
- Restrict CORS
- Add local API authentication
- Add trusted file boundaries
- Bind confirmations to exact tool arguments
- Remove `shell=True` from user-influenced execution paths

### Priority 2 — Reliability

- Add unit tests for memory, context, confirmation, and cache behavior
- Add FastAPI route tests
- Add provider-failover mocks
- Add planner/executor integration tests
- Add frontend smoke tests

### Priority 3 — Functional repairs

- Implement or remove the missing task-result API
- Regenerate the empty starter audio
- Reconnect Google OAuth
- Verify all dangerous-tool flags

### Priority 4 — Maintainability

- Split `app/main.py` into routers
- Split `web/script.js` into modules
- Split large services by responsibility
- Pin dependencies and create a lockfile
- Add structured logging and error identifiers

### Priority 5 — Future intelligence

- Memory viewer/edit UI
- Better alias confirmation flow
- More complete file watcher
- Trainer agent for corrections and skill improvement
- Permission profiles and trusted folders
- Checkpoint/rewind support
- Extension/MCP architecture

---

## 18. Troubleshooting

### Server does not start

- Confirm the virtual environment is active.
- Run `pip install -r requirements.txt`.
- Check whether port `8000` is already in use.
- Verify at least one valid LLM provider key is configured.

### Chat works but automation does not

- Confirm the application runs in an interactive Windows desktop session.
- Check pyautogui, pywinauto, pycaw, comtypes, and Windows SDK dependencies.
- Open the Control Center and inspect watcher/agent status.

### Voice input fails

- Check browser microphone permission.
- Confirm the audio upload reaches `/transcribe`.
- Inspect provider/key monitor errors.

### TTS fails

- Confirm internet access for Edge TTS.
- Verify `TTS_VOICE` and `TTS_RATE`.
- Clear only the affected voice-cache file and try again.

### Google tools fail

- Verify `credentials.json` exists and matches a desktop OAuth app.
- Revoke/remove an invalid saved token and authenticate again.
- Confirm the requested scopes are enabled.

### Memory does not persist

- Confirm `data/` is writable.
- Check that `memory.db` exists after startup.
- Inspect startup logs for the memory initialization message.
- Verify `MEMORY_ENABLED=True`.

---

## 19. Status Summary

| Area | Status |
|---|---|
| General chat | Implemented |
| Voice input/output | Implemented; real-machine verification required |
| Provider rotation/fallback | Implemented |
| Realtime web answers | Implemented |
| Desktop tools | Implemented; Windows verification required |
| Google services | Implemented; OAuth token may need renewal |
| Watcher | Implemented |
| Persistent memory | Implemented |
| Context engine | Implemented |
| Verification and skill learning | Implemented |
| Multi-step planner | Implemented |
| Command cache | Implemented |
| Proactive suggestions | Implemented |
| User model | Implemented |
| Automated test coverage | Incomplete |
| Security hardening | Required |
| Production readiness | Not ready |

---

## License

No license file is currently included. Add an explicit license before distributing the project publicly.
