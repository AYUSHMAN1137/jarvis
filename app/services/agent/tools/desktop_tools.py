"""
Desktop automation tools (Windows).

These run on the machine where the J.A.R.V.I.S server (run.py) is running.
For the standard localhost setup that is the user's own PC, so "open notepad"
opens Notepad on their screen.

Every function returns a short human-readable string describing what happened.
That string becomes the observation fed back to the LLM, so it can decide the
next step (e.g. after opening an app, type into it).
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from typing import Optional

from app.services.agent.tool_registry import tool
from app.services.debug_logger import dbg

logger = logging.getLogger("J.A.R.V.I.S")

# pyautogui / pygetwindow are imported lazily so the server still boots on a
# headless machine; tools just report a clear error if the GUI libs are missing.
try:
    import pyautogui
    from config import PYAUTOGUI_FAILSAFE
    pyautogui.FAILSAFE = PYAUTOGUI_FAILSAFE
    pyautogui.PAUSE = 0.15
    _GUI_OK = True
except Exception as _e:  # noqa: BLE001
    _GUI_OK = False
    logger.warning("[DESKTOP] pyautogui unavailable: %s", _e)


def _need_gui() -> Optional[str]:
    if not _GUI_OK:
        return "ERROR: GUI automation is unavailable on this machine (pyautogui not loaded)."
    return None


# M13 §4.3: the hand-written alias table (`_APP_ALIASES`) and the pronoun set
# (`_CLOSE_PRONOUNS`) are gone. Both were guesses about language, and every miss
# needed another line. What replaces them is discovery from the machine itself:
#   * `_resolve_launch_target` asks Windows what exists (PATH, then the Start
#     Menu the user launches things from anyway),
#   * `detect_reference` in the context engine is the single place that knows a
#     reference when it sees one, and the resolver has usually already turned
#     "close it" into a concrete app before the tool is ever called.


def _resolve_launch_target(name: str) -> tuple:
    """Ask Windows what `name` actually is. Returns (command, how).

    Order:
      1. an executable on PATH -- covers everything Windows ships (notepad, calc,
         mspaint, explorer, taskmgr, control, powershell, ...) without a table;
      2. a Start Menu shortcut -- covers apps installed into AppData (Telegram,
         Discord, WhatsApp, Spotify, VS Code, Office) that are NOT on PATH;
      3. the bare name, and let Windows' own app-registry resolution try.

    `how` is "path" | "shortcut" | "unknown" and drives the honesty check in
    open_application: an unknown name that produced no new process is reported as
    not found rather than as a success.
    """
    raw = (name or "").strip()
    if not raw:
        return ("", "unknown")
    import shutil
    found = shutil.which(raw)
    if found:
        return (raw, "path")
    # Windows apps are commonly asked for without the .exe.
    found = shutil.which(raw + ".exe")
    if found:
        return (raw, "path")
    shortcut = _find_app_shortcut(raw)
    if shortcut:
        return (shortcut, "shortcut")
    return (raw, "unknown")


def _looks_like_reference(text: str) -> bool:
    """Is this a reference to something rather than a name?

    Delegates to the context engine's `detect_reference`, which is the single
    owner of reference detection in the codebase. On any problem it returns False
    so a real app name is never rejected -- the worst case is a taskkill that
    finds nothing and says so.
    """
    try:
        from app.services.context.context_engine import detect_reference
        return bool(detect_reference(str(text or "")))
    except Exception:  # noqa: BLE001
        return False


def _resolve_window_reference(ref: str):
    """Phase 3: turn a VAGUE window reference ("this"/"isko"/""/"pehla wala") into
    a concrete window title / app name using the live context registry.

    Returns (needle, ask):
      - needle=str   -> use this as the (case-insensitive) match target
      - ask=str      -> low confidence; return this clarifying message to the user
      - (None, None) -> ref is already concrete, OR nothing to resolve; the
                        caller should just use the original ref.
    Fully fail-soft: any problem -> (None, None) so old behaviour is preserved.
    """
    try:
        from app.services.context.context_engine import (
            get_active_registry, detect_reference, learn_alias,
        )
    except Exception:  # noqa: BLE001
        return (None, None)
    raw = (ref or "").strip()
    # Only intervene for vague/empty references; concrete titles pass through.
    if raw and not detect_reference(raw):
        return (None, None)
    reg = get_active_registry()
    if reg is None:
        return (None, None)
    try:
        result = reg.resolve(raw or "this", type_hint=("window", "app"))
    except Exception:  # noqa: BLE001
        return (None, None)
    if result.matched and result.entity is not None:
        ent = result.entity
        needle = ent.handle.get("title") or ent.handle.get("name") or ent.label
        try:
            learn_alias(raw, ent)  # conservative; never learns pronouns/ordinals
        except Exception:  # noqa: BLE001
            pass
        logger.info("[DESKTOP] resolved reference %r -> %r", raw, needle)
        return (str(needle), None)
    if result.confidence == "low" and result.question:
        return (None, result.question)
    return (None, None)


def _find_app_shortcut(name: str) -> Optional[str]:
    """Search the Windows Start Menu for a shortcut (.lnk) matching ``name``.

    Many apps (Telegram, Discord, WhatsApp, Spotify, ...) install into the
    user's AppData and are NOT on PATH, so a bare ``start "telegram"`` fails
    with "The system cannot find the file telegram." Their Start Menu shortcut
    is the reliable, no-hardcode way to launch them exactly like the user would
    from the Start menu. Returns the full path to the best-matching .lnk, or None.
    """
    name = (name or "").strip().lower()
    if not name or os.name != "nt":
        return None
    roots = [
        os.path.join(
            os.environ.get("APPDATA", ""),
            "Microsoft", "Windows", "Start Menu", "Programs",
        ),
        os.path.join(
            os.environ.get("PROGRAMDATA", ""),
            "Microsoft", "Windows", "Start Menu", "Programs",
        ),
    ]
    best = None
    best_score = 99
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        try:
            for dirpath, _dirs, files in os.walk(root):
                for fname in files:
                    if not fname.lower().endswith(".lnk"):
                        continue
                    stem = fname[:-4].lower()
                    if stem == name:
                        score = 0
                    elif stem.startswith(name):
                        score = 1
                    elif name in stem:
                        score = 2
                    else:
                        continue
                    if score < best_score:
                        best_score = score
                        best = os.path.join(dirpath, fname)
                        if score == 0:
                            return best
        except Exception as e:  # noqa: BLE001
            logger.debug("[DESKTOP] shortcut scan failed in %s: %s", root, e)
    return best


@tool(
    name="open_application",
    description=(
        "Open / launch a desktop application or program on Windows by name. "
        "Use for apps like notepad, calculator, paint, chrome, vscode, word, "
        "file explorer, settings, etc. NOT for websites — use open_website for URLs."
    ),
    params={
        "app_name": {
            "type": "string",
            "description": "Name of the app to open, e.g. 'notepad', 'chrome', 'vs code'.",
        }
    },
    category="desktop",
    verification={"family": "open"},
)
def open_application(app_name: str) -> str:
    name = (app_name or "").strip().lower()
    if not name:
        return "ERROR: no app name given."
    dbg.uia_action("open_application", name)
    # Ask the machine what this name is, rather than consulting a table.
    command, how = _resolve_launch_target(name)
    discovered = how in ("path", "shortcut")
    # Snapshot running processes BEFORE launch so the watcher can figure out
    # exactly which new process belongs to this app. This is what makes
    # "close it" work later for unknown / UWP apps with no hardcoded map.
    watcher = None
    before: set = set()
    try:
        from app.services.watcher import get_watcher
        watcher = get_watcher()
        before = watcher.snapshot_pids()
    except Exception as e:  # noqa: BLE001
        logger.debug("[DESKTOP] watcher unavailable on open: %s", e)
    try:
        if command.startswith("start "):
            subprocess.Popen(command, shell=True)
        else:
            # `start "" <cmd>` lets Windows resolve registered app names.
            subprocess.Popen(f'start "" "{command}"', shell=True)
        logger.info("[DESKTOP] Opened application: %s -> %s", name, command)
        # Record the REAL process(es) that actually launched.
        launched = None
        if watcher is not None:
            try:
                launched = watcher.note_launch(name, before)
                if launched and launched.pids:
                    logger.info(
                        "[DESKTOP] Tracked '%s' as pid(s) %s",
                        name, sorted(launched.pids),
                    )
            except Exception as e:  # noqa: BLE001
                logger.debug("[DESKTOP] launch tracking failed: %s", e)
        else:
            time.sleep(0.6)
        # Honesty (reliability #1): if Windows did not recognise the name AND the
        # watcher saw NO new process appear, the app almost certainly isn't there
        # -- don't falsely claim success.
        if (
            watcher is not None
            and not discovered
            and (launched is None or not launched.pids)
        ):
            return (
                f"ERROR: I couldn't find an app called '{app_name}' on this PC — "
                "it is not on PATH and has no Start Menu entry. If you meant a "
                "Windows settings page, use open_settings_page instead; if you "
                "meant a website, use open_website."
            )
        return f"Opened {app_name}."
    except Exception as e:  # noqa: BLE001
        return f"ERROR: could not open '{app_name}': {e}"


@tool(
    name="close_application",
    description=(
        "Close / quit a running application. Pass the app name (e.g. 'notepad', "
        "'chrome', 'settings', 'calculator') OR say 'it' / 'this' to close the "
        "app that was most recently opened. Closes the REAL launched process, so "
        "it also works for unknown and Store/UWP apps."
    ),
    params={
        "app_name": {
            "type": "string",
            "description": (
                "App name to close, e.g. 'notepad', 'chrome'. Use 'it' or 'this' "
                "to close the most recently opened app."
            ),
            "required": False,
        }
    },
    category="desktop",
    verification={"family": "close"},
)
def close_application(app_name: str = "") -> str:
    name = (app_name or "").strip().lower()
    # 1) Smart path: ask the watcher to close the REAL tracked process.
    #    Handles "it"/"this"/"isko" (last opened), named apps, and unknown/UWP
    #    apps by their actual PID instead of a guessed executable name.
    try:
        from app.services.watcher import get_watcher
        res = get_watcher().close_by_name(name)
        if res.get("matched"):
            label = res.get("label") or app_name or "the app"
            logger.info(
                "[DESKTOP] Closed via watcher: %s (pids=%s)",
                label, res.get("killed"),
            )
            return f"Closed {label}."
    except Exception as e:  # noqa: BLE001
        logger.debug("[DESKTOP] watcher close failed, falling back: %s", e)
    # 2) A reference the watcher could not resolve -> we truly don't know which
    #    app is meant, and `taskkill /IM it.exe` is not an answer. Reference
    #    detection lives in the context engine, the one place that owns it.
    if not name or _looks_like_reference(name):
        return "I'm not sure which app to close — please tell me the app name."
    # 3) Fallback: taskkill by executable name, resolved from the machine.
    exe, _how = _resolve_launch_target(name)
    exe = os.path.basename(exe or name)
    if exe.lower().endswith(".lnk"):
        exe = exe[:-4]
    if not exe.lower().endswith(".exe"):
        exe = f"{exe}.exe"
    try:
        result = subprocess.run(
            ["taskkill", "/IM", exe, "/F"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            logger.info("[DESKTOP] Closed application (fallback): %s", exe)
            return f"Closed {app_name}."
        return f"Couldn't close '{app_name}' (maybe it isn't running)."
    except Exception as e:  # noqa: BLE001
        return f"ERROR: could not close '{app_name}': {e}"


@tool(
    name="type_text",
    description=(
        "Type text using the keyboard into whatever window is currently focused. "
        "Make sure the right app/field is focused first (open it, then type)."
    ),
    params={
        "text": {"type": "string", "description": "The exact text to type."},
        "press_enter": {
            "type": "boolean",
            "description": "If true, press Enter after typing.",
            "required": False,
        },
    },
    category="desktop",
    verification={"family": "input"},
)
def type_text(text: str, press_enter: bool = False) -> str:
    err = _need_gui()
    if err:
        return err
    if text is None:
        return "ERROR: no text given."
    try:
        pyautogui.typewrite(str(text), interval=0.01)
        if press_enter:
            pyautogui.press("enter")
        logger.info("[DESKTOP] Typed %d chars (enter=%s)", len(str(text)), press_enter)
        dbg.uia_action("type_text", text[:80], result=f"enter={press_enter}", ok=True)
        return f"Typed the text{' and pressed Enter' if press_enter else ''}."
    except Exception as e:  # noqa: BLE001
        return f"ERROR: typing failed: {e}"


@tool(
    name="press_hotkey",
    description=(
        "Press a single key or a keyboard shortcut combination. "
        "Examples: 'enter', 'esc', 'tab', or a combo like 'ctrl+s', 'alt+f4', "
        "'ctrl+shift+esc', 'win+d'. Separate combo keys with '+'."
    ),
    params={
        "keys": {
            "type": "string",
            "description": "Key or combo, e.g. 'enter' or 'ctrl+s'.",
        }
    },
    category="desktop",
    verification={"family": "input"},
)
def press_hotkey(keys: str) -> str:
    err = _need_gui()
    if err:
        return err
    # The planner sometimes passes keys as a list (e.g. ['ctrl', 's']) instead
    # of a '+'-joined string. Accept both so multi-step plans don't crash with
    # "'list' object has no attribute 'strip'".
    if isinstance(keys, (list, tuple)):
        keys = "+".join(str(k) for k in keys)
    combo = (keys or "").strip().lower()
    if not combo:
        return "ERROR: no keys given."
    try:
        parts = [k.strip() for k in combo.split("+") if k.strip()]
        if len(parts) == 1:
            pyautogui.press(parts[0])
        else:
            pyautogui.hotkey(*parts)
        logger.info("[DESKTOP] Pressed hotkey: %s", combo)
        dbg.uia_action("press_hotkey", combo, ok=True)
        return f"Pressed {combo}."
    except Exception as e:  # noqa: BLE001
        return f"ERROR: hotkey '{keys}' failed: {e}"


@tool(
    name="mouse_click",
    description=(
        "Move the mouse to screen coordinates (x, y) and click. "
        "Use 'left', 'right', or 'double' for the button. If x/y are omitted, "
        "clicks at the current cursor position."
    ),
    params={
        "x": {"type": "int", "description": "X pixel coordinate.", "required": False},
        "y": {"type": "int", "description": "Y pixel coordinate.", "required": False},
        "button": {
            "type": "string",
            "description": "Click type.",
            "required": False,
            "enum": ["left", "right", "double"],
        },
    },
    category="desktop",
    verification={"family": "input"},
)
def mouse_click(x: Optional[int] = None, y: Optional[int] = None, button: str = "left") -> str:
    err = _need_gui()
    if err:
        return err
    try:
        if x is not None and y is not None:
            if button == "double":
                pyautogui.doubleClick(int(x), int(y))
            else:
                pyautogui.click(int(x), int(y), button="right" if button == "right" else "left")
            where = f"at ({x}, {y})"
        else:
            if button == "double":
                pyautogui.doubleClick()
            else:
                pyautogui.click(button="right" if button == "right" else "left")
            where = "at the current position"
        logger.info("[DESKTOP] Mouse %s click %s", button, where)
        dbg.uia_action("mouse_click", where, result=f"button={button}", ok=True)
        return f"Performed a {button} click {where}."
    except Exception as e:  # noqa: BLE001
        return f"ERROR: mouse click failed: {e}"


@tool(
    name="scroll",
    description="Scroll the mouse wheel up or down by a number of clicks.",
    params={
        "amount": {
            "type": "int",
            "description": "Clicks to scroll. Positive = up, negative = down.",
        }
    },
    category="desktop",
    verification={"family": "input"},
)
def scroll(amount: int) -> str:
    err = _need_gui()
    if err:
        return err
    try:
        pyautogui.scroll(int(amount) * 100)
        return f"Scrolled {'up' if int(amount) >= 0 else 'down'}."
    except Exception as e:  # noqa: BLE001
        return f"ERROR: scroll failed: {e}"


@tool(
    name="take_screenshot",
    description=(
        "Capture the current screen and save it to disk. Returns the saved file "
        "path. Use when the user asks for a screenshot or when you need to see "
        "the screen state."
    ),
    params={},
    category="desktop",
    verification={"family": "file"},
)
def take_screenshot() -> str:
    err = _need_gui()
    if err:
        return err
    try:
        from config import CAMERA_CAPTURES_DIR
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = CAMERA_CAPTURES_DIR / f"screenshot_{ts}.png"
        img = pyautogui.screenshot()
        img.save(str(path))
        logger.info("[DESKTOP] Screenshot saved: %s", path)
        return f"Screenshot saved to {path}."
    except Exception as e:  # noqa: BLE001
        return f"ERROR: screenshot failed: {e}"


@tool(
    name="set_clipboard",
    description="Copy the given text to the system clipboard.",
    params={"text": {"type": "string", "description": "Text to copy to clipboard."}},
    category="desktop",
    verification={"family": "input"},
)
def set_clipboard(text: str) -> str:
    try:
        # pyautogui's typewrite-free clipboard via pyperclip if available,
        # else fall back to clip.exe on Windows.
        try:
            import pyperclip
            pyperclip.copy(str(text))
        except Exception:
            p = subprocess.Popen("clip", stdin=subprocess.PIPE, shell=True)
            p.communicate(input=str(text).encode("utf-16"))
        return "Copied text to the clipboard."
    except Exception as e:  # noqa: BLE001
        return f"ERROR: clipboard copy failed: {e}"


@tool(
    name="get_clipboard",
    description=(
        "Read the current text on the system clipboard. Use when the user says "
        "things like 'isko google karo', 'jo copy kiya hai', or asks what's on "
        "the clipboard."
    ),
    params={},
    category="desktop",
    verification={"family": "query", "cacheable": False},
)
def get_clipboard() -> str:
    try:
        import pyperclip
        text = pyperclip.paste() or ""
    except Exception as e:  # noqa: BLE001
        return f"ERROR: could not read the clipboard: {e}"
    text = str(text)
    if not text.strip():
        return "The clipboard is empty (or contains non-text content)."
    preview = text if len(text) <= 2000 else text[:2000] + " ...[truncated]"
    return f"Clipboard text: {preview}"


@tool(
    name="clipboard_history",
    description=(
        "List the things recently copied to the clipboard, newest first. Use for "
        "'what did I copy before', 'jo pehle copy kiya tha'. get_clipboard only "
        "returns the current item."
    ),
    params={
        "limit": {"type": "int", "required": False,
                  "description": "How many entries to return (default 10, max 20)."},
    },
    category="desktop",
    verification={"family": "query", "cacheable": False},
)
def clipboard_history(limit: int = 10) -> str:
    # Served from the watcher's existing tick -- no extra polling, and the
    # history is RAM-only so it never lands in a log or on disk.
    try:
        from app.services.watcher import get_watcher
        entries = get_watcher().clipboard_history(limit=min(20, max(1, int(limit or 10))))
    except Exception as e:  # noqa: BLE001
        return f"ERROR: clipboard history is unavailable: {e}"
    if not entries:
        return ("No clipboard history yet -- it starts filling once you copy "
                "something while JARVIS is running.")
    lines = [f"{i}. {entry}" for i, entry in enumerate(entries, 1)]
    return f"Recent clipboard items ({len(entries)}):\n" + "\n".join(lines)


def _activate_window(w) -> None:
    """Restore + bring a pygetwindow window to the front.

    pygetwindow/pywin32 raises a SPURIOUS error even when the window actually
    came to the front: ``Error code from Windows: 0 -- The operation completed
    successfully.`` We treat winerror 0 / 'completed successfully' as success,
    and fall back to a direct Win32 foreground call before giving up.
    """
    def _benign(err) -> bool:
        m = str(err).lower()
        return "completed successfully" in m or "error code from windows: 0" in m

    try:
        if w.isMinimized:
            w.restore()
    except Exception as e:  # noqa: BLE001
        if not _benign(e):
            raise
    try:
        w.activate()
    except Exception as e:  # noqa: BLE001
        if _benign(e):
            return
        try:
            import win32gui  # type: ignore

            win32gui.SetForegroundWindow(w._hWnd)
        except Exception as e2:  # noqa: BLE001
            if not _benign(e2):
                raise


@tool(
    name="focus_window",
    description=(
        "Bring a window to the front and focus it by (partial) title match, "
        "e.g. 'Notepad', 'Chrome'. Useful before typing into a specific window."
    ),
    params={
        "title": {
            "type": "string",
            "description": "Part of the window title to match.",
        }
    },
    category="desktop",
    verification={"family": "input"},
)
def focus_window(title: str) -> str:
    try:
        import pygetwindow as gw
    except Exception as e:  # noqa: BLE001
        return f"ERROR: window control unavailable: {e}"
    # Phase 3: resolve vague references ("focus this", "isko saamne lao") to a
    # concrete window title using live context before matching.
    resolved, ask = _resolve_window_reference(title)
    if ask:
        return ask
    needle = (resolved or title or "").strip().lower()
    if not needle:
        return "ERROR: no window title given."
    try:
        for w in gw.getAllWindows():
            if needle in (w.title or "").lower():
                _activate_window(w)
                logger.info("[DESKTOP] Focused window: %s", w.title)
                time.sleep(0.3)
                return f"Focused the window '{w.title}'."
        return f"No open window matching '{title}' was found."
    except Exception as e:  # noqa: BLE001
        return f"ERROR: focusing window failed: {e}"


@tool(
    name="window_action",
    description=(
        "Minimize, maximize, restore, or close the window matching a title. "
        "Action is one of: minimize, maximize, restore, close."
    ),
    params={
        "title": {"type": "string", "description": "Part of the window title."},
        "action": {
            "type": "string",
            "description": "What to do with the window.",
            "enum": ["minimize", "maximize", "restore", "close"],
        },
    },
    category="desktop",
    verification={"family": "input"},
)
def window_action(title: str, action: str) -> str:
    try:
        import pygetwindow as gw
    except Exception as e:  # noqa: BLE001
        return f"ERROR: window control unavailable: {e}"
    # Phase 3: resolve vague references ("minimize this", "isko band karo") to a
    # concrete window title using live context before matching.
    resolved, ask = _resolve_window_reference(title)
    if ask:
        return ask
    needle = (resolved or title or "").strip().lower()
    act = (action or "").strip().lower()
    try:
        for w in gw.getAllWindows():
            if needle in (w.title or "").lower():
                if act == "minimize":
                    w.minimize()
                elif act == "maximize":
                    w.maximize()
                elif act == "restore":
                    w.restore()
                elif act == "close":
                    w.close()
                else:
                    return f"ERROR: unknown window action '{action}'."
                logger.info("[DESKTOP] Window %s: %s", act, w.title)
                return f"Did '{act}' on window '{w.title}'."
        return f"No open window matching '{title}' was found."
    except Exception as e:  # noqa: BLE001
        return f"ERROR: window action failed: {e}"


@tool(
    name="list_open_windows",
    description=(
        "List the application windows currently open on screen (from the live "
        "system watcher). Useful to see what is running before closing/focusing."
    ),
    params={},
    category="desktop",
    verification={"family": "query", "cacheable": False},
)
def list_open_windows() -> str:
    try:
        from app.services.watcher import get_watcher
        windows = get_watcher().list_open_windows()
        if not windows:
            return "No open application windows detected."
        return "Open windows: " + "; ".join(windows)
    except Exception as e:  # noqa: BLE001
        return f"ERROR: could not list open windows: {e}"
