# J.A.R.V.I.S — Manual Test Command Sheet

Run these, then send me the logs. The UI-control engine has been rebuilt, so this
round is about finding what still breaks in real use rather than proving it runs
at all.

---

## 0. Before you start

**Restart the server.** All of the changes load at startup.

```cmd
.venv\Scripts\python.exe run.py
```

In the startup output you should see these four lines. If any is missing, stop
and send me the log — nothing below will work properly.

```
[UI-THREAD] gen=1 apartment=sta ready
[UIA] backend ready: pywinauto (apartment gen=1)
[UIA] warmed up in 0.0Xs (apartment=sta)
[STARTUP] UI automation ready.
```

**Watch the live log in a second terminal:**

```cmd
.venv\Scripts\python.exe scripts\view_debug_log.py
```

| Command | Shows |
|---|---|
| `scripts\view_debug_log.py --uia` | window resolution, tree size, candidate scores, click method |
| `scripts\view_debug_log.py --tools` | tool calls and results with timings |
| `scripts\view_debug_log.py --llm` | what the model chose, and its reasoning |
| `scripts\view_debug_log.py --errors` | failures, timeouts, tracebacks |
| `scripts\view_debug_log.py --verify` | checker verdicts and cache decisions |
| `scripts\view_debug_log.py --turn 7` | just turn 07 |
| `scripts\view_debug_log.py -l` | list the log files |

Logs live in `data/debug_logs/`:

- `trace.log` — everything, continuous. **Send me this one.**
- `session_<ts>_<sid>.log` — one file per chat session, all its turns
- `server.log` — console log, third-party tracebacks

Each turn is banner-separated and numbered `[T01]`, so "test 14 failed" is enough
for me to find it.

---

## 1. Baseline

| # | Command | Expected |
|---|---|---|
| 1 | `hello, who are you` | conversational reply, no tools |
| 2 | `what time is it` | current time |
| 3 | `open notepad` | Notepad opens |
| 4 | `set volume to 30` | volume changes, verdict PASS |
| 5 | `mute the volume` | muted |
| 6 | `set brightness to 70` | brightness changes |
| 7 | `take a screenshot` | screenshot saved (this was broken before — Pillow was missing) |
| 8 | `what windows are open` | your real windows |
| 9 | `close notepad` | closes |
| 10 | `turn off bluetooth` then `turn it on` | both work, both verified |

---

## 2. UI control — the rebuilt path

Watch `--uia` during these. `ui_do` is the new tool: it finds the control itself,
searching and scrolling when needed.

### 2a. Settings

| # | Command | What must happen |
|---|---|---|
| 11 | `open night light settings` | Settings opens on that page |
| 12 | `turn off night light` | the toggle actually flips, reply says so |
| 13 | `turn on night light` | flips back |
| 14 | `check for updates` | Settings opens **and** the update button is pressed |
| 15 | `open display settings and tell me what options are there` | lists REAL control names from the page, not general knowledge |
| 16 | `is there any option called graphics in display settings` | answers by looking, and says no if it isn't there |
| 17 | `open bluetooth settings and turn bluetooth off` | switch flips |
| 18 | `open sound settings and change the output device` | reaches the device picker (not just "opened Settings") |
| 19 | `turn on airplane mode` then `turn it off` | both apply |
| 20 | `open windows update and click download` | clicks it, or says honestly there is nothing to download |

### 2b. Desktop apps

| # | Command | What must happen |
|---|---|---|
| 21 | `open notepad and type hello world` | text appears |
| 22 | `write a short poem and save it on my desktop` | a real file on YOUR Desktop (should use write_file, not the Save dialog) |
| 23 | `open calculator and calculate 45 times 12` | 540 shown |
| 24 | `open notepad and make the text bold` | bold button pressed |
| 25 | `open task manager and show me cpu usage` | right tab |
| 26 | `open file explorer and go to downloads` | navigates |

### 2c. Browser

| # | Command | What must happen |
|---|---|---|
| 27 | `play talwinder song on youtube` | video plays; reply must not claim playback it didn't verify |
| 28 | `open youtube and play arijit singh` | plays |
| 29 | `search python tutorials on youtube and play the first video` | first result clicked |
| 30 | `pause the video` | pauses |
| 31 | `make the youtube video fullscreen` | fullscreen |
| 32 | `open google and search for weather in delhi` | search runs |

---

## 3. Multi-step

These should no longer ask "should I go ahead?" for ordinary requests.

| # | Command | Expected |
|---|---|---|
| 33 | `turn wifi on and set brightness to 50` | both, both verified |
| 34 | `open bluetooth settings, turn it off, then close settings` | all three steps |
| 35 | `open notepad and calculator both` | both open |
| 36 | `open youtube, play a song, and set volume to 60` | all three |
| 37 | `shutdown the computer` | **asks for confirmation** (this one still should) |

---

## 4. Honesty — it must not claim false success

This is the most important section. Note any test where it says something is done
but the screen disagrees.

| # | Command | Expected |
|---|---|---|
| 38 | `turn on night light` then, after it replies, `nothing happened` | it RETRIES the action; it must not just say "Done" |
| 39 | `turn off night light` then `it's still on` | retries and verifies |
| 40 | `click the purple elephant button` | honest failure, lists what it can see |
| 41 | `open a program called xyzabc123` | honest "couldn't find it" |
| 42 | `install photoshop for me` | honest limitation |
| 43 | Say nothing / let the mic pick up silence | "I didn't catch that", no agent run |
| 44 | Ask it to do something risky, then reply `Yup.` | "Yup." must be accepted as yes |

---

## 5. Command cache — does it remember now?

Run each pair. The **second** one should be much faster and show a cache hit in
`--verify`.

| # | First | Then | Expected |
|---|---|---|---|
| 45 | `turn off bluetooth` | `bluetooth off karo` | paraphrase HIT |
| 46 | `set volume to 30` | `volume 30 kar do` | paraphrase HIT |
| 47 | `set volume to 30` | `set volume to 70` | MISS (different number — must not reuse) |
| 48 | `open notepad` | `close notepad` | MISS (opposite action — must not reuse) |
| 49 | `check for updates` | `click check update` | paraphrase HIT |
| 50 | `turn on night light` | `turn off night light` | MISS (opposite state) |

Tests 47, 48 and 50 are the safety ones. A hit there would be a bug — tell me
immediately if you see one.

---

## 6. Hindi / mixed

| # | Command | Expected |
|---|---|---|
| 51 | `notepad kholo` | opens |
| 52 | `volume 50 kar do` | volume 50 |
| 53 | `bluetooth band kar do` | off |
| 54 | `night light chalu karo` | on |
| 55 | `youtube pe talwinder ka gana chala do` | plays |
| 56 | `screenshot lo` | taken |

---

## 7. Direct engine probe (no LLM)

When a UI test fails, run the probe. It removes the model from the loop, so it
tells us whether the failure is the engine or the model's choice of tool.

```cmd
:: is UI automation alive?
.venv\Scripts\python.exe scripts\uia_probe.py status

:: what windows can be targeted
.venv\Scripts\python.exe scripts\uia_probe.py windows

:: what is in a window (named, operable controls with on/off state)
.venv\Scripts\python.exe scripts\uia_probe.py list --window Settings
.venv\Scripts\python.exe scripts\uia_probe.py list --window Chrome --type Hyperlink

:: can it locate a control, and with what score
.venv\Scripts\python.exe scripts\uia_probe.py find --window Settings --name "Night light"

:: can it press it
.venv\Scripts\python.exe scripts\uia_probe.py click --window Settings --name "Turn off now"

:: toggles, typing, scrolling
.venv\Scripts\python.exe scripts\uia_probe.py toggle --window Settings --name "Schedule night light" --on
.venv\Scripts\python.exe scripts\uia_probe.py type --window Notepad --name "Text editor" --text hello
.venv\Scripts\python.exe scripts\uia_probe.py scroll --window Settings --direction down

:: the full navigation loop: search / drill in / scroll, then act
.venv\Scripts\python.exe scripts\uia_probe.py reach --window Settings --name "Night light" --action click
```

Run at least these:

| # | Probe |
|---|---|
| P1 | `status` — must show `apartment=sta`, `retired=0` |
| P2 | `windows` — with Settings, Chrome and Notepad open |
| P3 | `list --window Settings` — on the Night light page |
| P4 | `list --window Chrome` — with a YouTube search page loaded |
| P5 | `find --window Chrome --name "<first video title>"` |
| P6 | `reach --window Settings --name "Windows Update" --action click` |

If `status` ever shows `retired` above 0, a UI call wedged — send me the log.

---

## 8. Quick automated check

```cmd
.venv\Scripts\python.exe -m pytest tests\ -q
.venv\Scripts\python.exe scripts\smoke_test.py "open notepad" "close notepad"
```

---

## 9. What to send me

1. `data/debug_logs/trace.log`
2. `data/debug_logs/server.log`
3. Which test numbers failed, and what you saw on screen.

Point 3 matters most. The log tells me what the software believed happened. Only
you can tell me what the screen actually did. The gap between those two is where
the remaining bugs are.
