# Future Jarvis — Windows UI Automation Architecture

## Overview

The "future jarvis" project uses a **two-layer automation stack** controlled by an **LLM-driven ReAct agent loop**. The LLM (Gemini/Groq) decides which tools to call, chains multiple steps, and adapts on failure — no hardcoded per-app logic.

```
┌─────────────────────────────────────────────────────────┐
│                    USER COMMAND                          │
│         "YouTube pe Arijit Singh ka music chalao"        │
└────────────────────────┬────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────┐
│              FAST-PATH (try_direct_web_command)          │
│  Regex match: "play X" → play_on_youtube tool directly  │
│  (Skips LLM for obvious mechanical commands)            │
└────────────────────────┬────────────────────────────────┘
                         ▼ (if not fast-path)
┌─────────────────────────────────────────────────────────┐
│              REACT AGENT LOOP (agent_loop.py)            │
│                                                         │
│  1. Build messages (system prompt + state block + user) │
│  2. Send to LLM with all tool schemas                   │
│  3. LLM returns tool_calls → execute each               │
│  4. Feed results back → LLM decides next step           │
│  5. Repeat until LLM gives final text answer            │
│  (Max 10 steps, dangerous actions pause for confirm)    │
└────────────────────────┬────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────┐
│                    TOOL LAYER                            │
│                                                         │
│  ┌─────────────────┐    ┌──────────────────────────┐   │
│  │  LAYER 1:       │    │  LAYER 2:                │   │
│  │  pyautogui      │    │  pywinauto (UIA backend) │   │
│  │  (coordinates)  │    │  (semantic/name-based)   │   │
│  │                 │    │                          │   │
│  │  • mouse_click  │    │  • ui_click (by name)   │   │
│  │  • type_text    │    │  • ui_set_toggle        │   │
│  │  • press_hotkey │    │  • ui_type_into         │   │
│  │  • scroll       │    │  • ui_list_controls     │   │
│  │  • screenshot   │    │                          │   │
│  │  • clipboard    │    │  Walks the Windows UI    │   │
│  │                 │    │  Automation tree, finds  │   │
│  │  Low-level,     │    │  controls by NAME +     │   │
│  │  pixel-based    │    │  TYPE, then invokes/    │   │
│  │                 │    │  toggles/types into them │   │
│  └─────────────────┘    └──────────────────────────┘   │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │  LAYER 3: Window Management (pygetwindow)       │   │
│  │  • focus_window, window_action, list_windows    │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │  LAYER 4: App Launch (subprocess + Start Menu)  │   │
│  │  • open_application (alias map + .lnk search)   │   │
│  │  • close_application (PID tracking via watcher) │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

---

## Layer 2 Deep Dive: The UIA Engine (`uia_engine.py`)

This is the **core innovation** — a generic, name-based Windows UI control driver.

### Design Principles

| Principle | Implementation |
|-----------|---------------|
| **No hardcode** | Controls matched by fuzzy name tokens, never by coordinates or app-specific IDs |
| **Fail-soft** | Every call wrapped in try/except; returns `{ok: False, reason: ...}` — NEVER raises, NEVER fakes success |
| **Lazy + optional** | pywinauto imported on first use; server boots fine without it |
| **Backend-agnostic** | Duck-typed interface — can swap pywinauto for any other UIA library |
| **Testable** | Mock backend injectable for unit tests without Windows |

### Backend Contract (Duck-Typed)

```python
# A backend exposes:
backend.available() -> bool
backend.find(name, window, control_type, timeout) -> list[control]

# Each control exposes:
control.name           -> str          # visible text
control.control_type   -> str          # "Button", "CheckBox", "Edit", etc.
control.toggle_state   -> bool | None  # None if not a toggle
control.invoke()       -> None         # click/press
control.toggle(on)     -> None         # set toggle/checkbox
control.set_text(text) -> None         # type into edit field
```

### Fuzzy Name Matching Algorithm

```python
def match_score(target, candidate) -> float:
    # 1.0  → exact match (normalized)
    # 0.9  → substring match (target in candidate or vice versa)
    # <0.9 → fraction of target tokens found in candidate
    # 0.0  → nothing in common
    
    # Minimum score to accept: 0.5
```

Example: target="Play" matches candidate="Play video" → score 0.9 (substring)

### The Pywinauto Backend

```python
class _PywinautoBackend:
    def __init__(self):
        from pywinauto import Desktop
        self._desktop = Desktop(backend="uia")  # Windows UI Automation

    def find(self, name, window, control_type, timeout):
        win = self._window(window)  # by title regex or active window
        descendants = win.descendants()  # walk entire UI tree
        # Filter by control_type, wrap each in _PywinautoControl adapter
        return [_PywinautoControl(el) for el in descendants if type_matches]
```

### Control Adapter (wraps raw pywinauto elements)

```python
class _PywinautoControl:
    def invoke(self):
        try:
            self._el.invoke()       # UIA Invoke pattern
        except:
            self._el.click_input()  # fallback: physical click

    def toggle(self, on):
        try:
            self._el.toggle()       # UIA Toggle pattern
        except:
            self.invoke()           # fallback: click to toggle

    def set_text(self, text):
        try:
            self._el.set_edit_text(text)  # UIA Value pattern
        except:
            self._el.set_focus()
            self._el.type_keys(text)      # fallback: keyboard input
```

---

## Layer 1: pyautogui Tools (`desktop_tools.py`)

Low-level, coordinate-based automation for when semantic UIA isn't enough:

| Tool | What it does |
|------|-------------|
| `type_text` | `pyautogui.typewrite()` into focused window |
| `press_hotkey` | `pyautogui.hotkey("ctrl", "s")` or `pyautogui.press("enter")` |
| `mouse_click` | Click at (x, y) coordinates — left/right/double |
| `scroll` | `pyautogui.scroll(amount * 100)` |
| `take_screenshot` | `pyautogui.screenshot()` → save PNG |
| `set_clipboard` | `pyperclip.copy(text)` |
| `get_clipboard` | `pyperclip.paste()` |

---

## Layer 3: Window Management

| Tool | Library | What it does |
|------|---------|-------------|
| `focus_window` | pygetwindow | Find window by partial title → `activate()` |
| `window_action` | pygetwindow | minimize / maximize / restore / close |
| `list_open_windows` | watcher | List all visible window titles |

**Smart reference resolution**: "focus this" / "isko saamne lao" → resolved via ContextRegistry (tracks last opened app, active window, conversation history).

---

## Layer 4: App Launch & Process Tracking

```python
def open_application(app_name):
    # 1. Check alias map: "vs code" → "code", "calculator" → "calc"
    # 2. Search Start Menu .lnk shortcuts (covers Telegram, Discord, etc.)
    # 3. Fallback: `start "" "name"` (Windows resolves via PATH/registry)
    # 4. Track launched PID via watcher (enables "close it" later)
    # 5. HONESTY: if no process appeared → "couldn't find app"
```

---

## The ReAct Agent Loop (`agent_loop.py`)

### Flow

```
User message
    ↓
Build messages: [system_prompt, state_block, history(6), user_msg]
    ↓
┌─── LOOP (max 10 steps) ───────────────────────────┐
│  Send messages + tool schemas to LLM              │
│       ↓                                           │
│  LLM responds with tool_calls OR final text       │
│       ↓                                           │
│  If tool_calls:                                   │
│    - Check dangerous → pause for user confirm     │
│    - Execute each tool via registry.execute()     │
│    - Record action in memory                      │
│    - Publish to Phase4 verifier (background)      │
│    - Feed result back into messages               │
│    - Continue loop                                │
│  If final text:                                   │
│    - Break → return answer                        │
└───────────────────────────────────────────────────┘
    ↓
Emit frontend actions (open URL, play video, etc.)
    ↓
Return final spoken answer
```

### System Prompt (what the LLM sees)

```
You are J.A.R.V.I.S, an AI assistant that controls the user's Windows 
computer and browser through tools.

Rules:
- Use tools to perform any real action. Do not pretend.
- For desktop apps use open_application. For web use open_website.
- After a tool returns, read its result. If "ERROR", adapt.
- When a step needs a window focused before typing, focus it first.
- Keep going until fully done, then give short spoken-style reply.
- If impossible with available tools, say so honestly.
```

### State Block (injected context)

The agent also gets a **live system state block** built from:
- Active window title
- Open applications list
- Clipboard content
- Recent conversation (for reference resolution)
- Last action performed
- Toggle states (Bluetooth, WiFi, etc.)

This lets the LLM resolve vague commands like "close it" or "type in this".

---

## Web Tools — Frontend Action Pattern

Web tools DON'T run on the server. They emit **frontend actions** via a thread-local `action_sink`:

```python
def play_on_youtube(query):
    url = f"https://www.youtube.com/results?search_query={quote(query)}"
    action_sink.add_play(url)  # collected → sent to browser client
    return f"Playing '{query}' on YouTube."
```

The browser (React frontend) receives the action and opens the URL client-side.

### Fast-Path (Deterministic, No LLM)

For obvious commands, a regex-based `try_direct_web_command()` bypasses the LLM entirely:

```python
# "play despacito" → {"tool": "play_on_youtube", "args": {"query": "despacito"}}
# "open youtube"   → {"tool": "open_website", "args": {"target": "youtube"}}
# "search X"       → {"tool": "search_google", "args": {"query": "X"}}
```

Supports English + Hindi verbs: `open|launch|kholo|khol do`, `play|chalao|bajao`, etc.

---

## Complete Tool Inventory (20+ tools)

### Desktop (pyautogui + subprocess)
| Tool | Category |
|------|----------|
| `open_application` | Launch apps (alias + Start Menu .lnk) |
| `close_application` | Kill by PID (watcher) or taskkill |
| `type_text` | Keyboard input into focused window |
| `press_hotkey` | Single key or combo (ctrl+s, alt+f4) |
| `mouse_click` | Click at coordinates (left/right/double) |
| `scroll` | Mouse wheel up/down |
| `take_screenshot` | Capture screen → PNG |
| `set_clipboard` / `get_clipboard` | Copy/paste text |
| `focus_window` | Bring window to front |
| `window_action` | Minimize/maximize/restore/close |
| `list_open_windows` | List all visible windows |

### UI Automation (pywinauto UIA)
| Tool | Category |
|------|----------|
| `ui_click` | Click any control by visible name |
| `ui_set_toggle` | Flip any toggle/checkbox by name |
| `ui_type_into` | Type into any text field by name |
| `ui_list_controls` | Discover controls in a window |

### Web (frontend actions)
| Tool | Category |
|------|----------|
| `open_website` | Open URL in browser |
| `play_on_youtube` | YouTube search + play |
| `search_google` | Google search |
| `search_youtube` | YouTube search (no autoplay) |

---

## Key Takeaways / Ideas to Borrow

1. **Two-layer approach**: pyautogui for pixel-level (mouse, keyboard, screenshot) + pywinauto UIA for semantic (click by name, toggle by name). Use the right layer for the job.

2. **Fuzzy name matching**: Token-based scoring (exact=1.0, substring=0.9, token overlap=fraction) with a 0.5 threshold. No brittle exact-match.

3. **Fail-soft everywhere**: Every tool returns `{ok: bool, reason: str}` — never crashes the server, never lies about success.

4. **LLM decides the chain**: The ReAct loop lets the LLM call multiple tools in sequence, adapting based on results. No hardcoded "if YouTube then do X, Y, Z".

5. **Context Registry**: Tracks what's on screen (active window, open apps, last action) so vague commands like "close it" / "type in this" resolve correctly.

6. **Fast-path for obvious commands**: Regex-based shortcut for "play X" / "open Y" / "search Z" — instant, no LLM latency.

7. **Honest failure**: If an app can't be found, if a control isn't visible, the system says so clearly instead of pretending success.

8. **Process tracking via Watcher**: Snapshots PIDs before launch, diffs after → knows exactly which process belongs to "the app I just opened".

9. **Dangerous action gate**: shutdown/delete/etc. pause and ask user to confirm before executing.

10. **Provider failover**: Gemini (primary) → Groq (fallback), with per-key circuit breakers and rate-limit handling.
