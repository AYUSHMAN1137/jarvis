"""Phase 5 -- generic UI Automation tools.

These expose the UIA engine (see automation/uia_engine.py) to the agent as
ordinary tools, so the LLM can click ANY on-screen control, flip ANY toggle, or
type into ANY field -- by NAME, with no per-app hardcode.

This is the real capability that was missing before: with these the agent can
actually press a YouTube 'Play' button, flip a Settings toggle that the radio
shortcuts don't cover, or pick a menu item.

All tools are fail-soft: if pywinauto isn't installed (e.g. in the dev sandbox)
they return a short, honest message instead of crashing -- they NEVER claim a
click happened when it didn't.
"""

from __future__ import annotations

import logging

from app.services.agent.tool_registry import tool
from app.services.agent.automation import get_uia_engine
from app.services.debug_logger import dbg

logger = logging.getLogger("J.A.R.V.I.S")


def _say(result: dict, ok_msg: str) -> str:
    """Turn an engine result dict into a short human string for the agent."""
    if result.get("ok"):
        ev = result.get("evidence")
        return ev if ev else ok_msg
    reason = result.get("reason") or "action failed"
    cands = result.get("candidates") or []
    if cands:
        shown = ", ".join(str(c) for c in cands[:6] if c)
        if shown:
            return f"ERROR: {reason}. Visible controls: {shown}"
    return f"ERROR: {reason}"


@tool(
    name="ui_click",
    description=(
        "Click a real on-screen UI control by its visible name (button, link, "
        "menu item, list item, tab, etc.). Use this when opening an app or a URL "
        "is not enough and you must actually press something on screen -- e.g. "
        "press a 'Play' button, an 'OK'/'Save' button, or pick a menu entry. "
        "Optionally narrow with the window title or the control type."
    ),
    params={
        "name": {"type": "string", "description": "Visible text/name of the control to click."},
        "window": {"type": "string", "description": "Optional window title to search inside.", "required": False},
        "control_type": {"type": "string", "description": "Optional control type, e.g. Button, MenuItem, ListItem, Hyperlink.", "required": False},
    },
    category="system",
)
def ui_click(name: str, window: str = "", control_type: str = "") -> str:
    eng = get_uia_engine()
    dbg.uia_action("click", name, window=window)
    res = eng.click(name, window=window or None, control_type=control_type or None)
    ok = res.get("ok", False)
    dbg.uia_action("click_result", name, window=window, result=str(res.get("evidence") or res.get("reason", "")), ok=ok)
    return _say(res, f"Clicked '{name}'.")


@tool(
    name="ui_set_toggle",
    description=(
        "Turn an on-screen toggle / switch / checkbox on or off by its visible "
        "name (e.g. a Settings switch like 'Bluetooth', 'Airplane mode', a "
        "checkbox in a dialog). It first checks the current state and does "
        "nothing if it is already in the wanted state."
    ),
    params={
        "name": {"type": "string", "description": "Visible name of the toggle/switch/checkbox."},
        "on": {"type": "boolean", "description": "True to turn on, False to turn off."},
        "window": {"type": "string", "description": "Optional window title to search inside.", "required": False},
    },
    category="system",
)
def ui_set_toggle(name: str, on: bool = True, window: str = "") -> str:
    eng = get_uia_engine()
    dbg.uia_action(f"set_toggle({'on' if on else 'off'})", name, window=window)
    res = eng.set_toggle(name, bool(on), window=window or None)
    ok = res.get("ok", False)
    dbg.uia_action("toggle_result", name, window=window, result=str(res.get("evidence") or res.get("reason", "")), ok=ok)
    return _say(res, f"Set '{name}' {'on' if on else 'off'}.")


@tool(
    name="ui_type_into",
    description=(
        "Type text into a named on-screen text field / edit box (e.g. a search "
        "box or form field). Finds the field by its visible name/label."
    ),
    params={
        "name": {"type": "string", "description": "Visible name/label of the text field."},
        "text": {"type": "string", "description": "Text to type into the field."},
        "window": {"type": "string", "description": "Optional window title to search inside.", "required": False},
    },
    category="system",
)
def ui_type_into(name: str, text: str, window: str = "") -> str:
    eng = get_uia_engine()
    dbg.uia_action("type_into", name, window=window, result=f"text='{text[:50]}'")
    res = eng.set_text(name, text, window=window or None)
    ok = res.get("ok", False)
    dbg.uia_action("type_result", name, window=window, result=str(res.get("evidence") or res.get("reason", "")), ok=ok)
    return _say(res, f"Typed into '{name}'.")


@tool(
    name="ui_list_controls",
    description=(
        "List the visible, interactable controls in the current (or a named) "
        "window -- useful to discover the exact button/toggle names before "
        "clicking. Optionally filter by control type. If the window was just "
        "opened, this tool automatically retries after a short delay if no "
        "controls are found yet (pages take 1-3s to load)."
    ),
    params={
        "window": {"type": "string", "description": "Optional window title to inspect.", "required": False},
        "control_type": {"type": "string", "description": "Optional filter, e.g. Button, CheckBox, MenuItem, Hyperlink.", "required": False},
    },
    category="system",
)
def ui_list_controls(window: str = "", control_type: str = "") -> str:
    import time as _time
    eng = get_uia_engine()
    dbg.uia_action("list_controls", window or "(active)", window=window)
    res = eng.list_controls(window=window or None, control_type=control_type or None)
    if not res.get("ok"):
        dbg.uia_action("list_controls_FAIL", window or "(active)", result=res.get("reason", ""), ok=False)
        return f"ERROR: {res.get('reason', 'could not list controls')}"
    items = res.get("controls") or []
    # Auto-retry: pages/windows take 1-3s to load. If nothing found, wait and retry.
    if not items:
        dbg.info("UIA", "No controls found on first try, retrying after 2.5s...")
        _time.sleep(2.5)
        res = eng.list_controls(window=window or None, control_type=control_type or None)
        if res.get("ok"):
            items = res.get("controls") or []
    if not items:
        dbg.uia_action("list_controls_EMPTY", window or "(active)", result="No controls found even after retry", ok=False)
        return "No interactable controls found in that window. The window may not be open or may still be loading."
    dbg.uia_action("list_controls_OK", window or "(active)", result=f"{len(items)} controls found", ok=True)
    lines = []
    for it in items[:25]:
        tg = it.get("toggle")
        suffix = "" if tg is None else (" [on]" if tg else " [off]")
        lines.append(f"- {it.get('name') or '?'} ({it.get('type') or '?'}){suffix}")
    return "Controls:\n" + "\n".join(lines)


@tool(
    name="ui_wait",
    description=(
        "Wait/pause for a given number of seconds before the next action. "
        "Use this when a window or page needs time to load before you can "
        "interact with it (e.g. after opening a browser page or an app)."
    ),
    params={
        "seconds": {"type": "number", "description": "Number of seconds to wait (1-10)."},
    },
    category="system",
)
def ui_wait(seconds: float = 3.0) -> str:
    import time as _time
    secs = max(0.5, min(float(seconds), 10.0))
    _time.sleep(secs)
    return f"Waited {secs:.1f} seconds."
