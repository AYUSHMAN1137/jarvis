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
    res = eng.click(name, window=window or None, control_type=control_type or None)
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
    res = eng.set_toggle(name, bool(on), window=window or None)
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
    res = eng.set_text(name, text, window=window or None)
    return _say(res, f"Typed into '{name}'.")


@tool(
    name="ui_list_controls",
    description=(
        "List the visible, interactable controls in the current (or a named) "
        "window -- useful to discover the exact button/toggle names before "
        "clicking. Optionally filter by control type."
    ),
    params={
        "window": {"type": "string", "description": "Optional window title to inspect.", "required": False},
        "control_type": {"type": "string", "description": "Optional filter, e.g. Button, CheckBox, MenuItem.", "required": False},
    },
    category="system",
)
def ui_list_controls(window: str = "", control_type: str = "") -> str:
    eng = get_uia_engine()
    res = eng.list_controls(window=window or None, control_type=control_type or None)
    if not res.get("ok"):
        return f"ERROR: {res.get('reason', 'could not list controls')}"
    items = res.get("controls") or []
    if not items:
        return "No interactable controls found in that window."
    lines = []
    for it in items[:25]:
        tg = it.get("toggle")
        suffix = "" if tg is None else (" [on]" if tg else " [off]")
        lines.append(f"- {it.get('name') or '?'} ({it.get('type') or '?'}){suffix}")
    return "Controls:\n" + "\n".join(lines)
