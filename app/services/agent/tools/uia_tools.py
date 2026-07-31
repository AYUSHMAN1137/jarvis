"""UI-control tools -- let the agent operate real on-screen controls.

These are the tools that turn "open the window" into "do the thing inside the
window". Every one of them:

  * runs on the dedicated UI apartment (no COM deadlock),
  * reports what it actually observed, never a hopeful "done",
  * on failure returns the real control names it could see, so the agent's next
    attempt is informed instead of another guess.

`ui_do` is the one to prefer: it navigates (search / drill-in / scroll) to reach
a control that is not on screen yet, which is what most real requests need.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from app.services.agent.automation import get_uia_engine
from app.services.agent.automation.navigator import get_navigator
from app.services.agent.tool_registry import tool
from app.services.debug_logger import dbg

logger = logging.getLogger("J.A.R.V.I.S")


# --------------------------------------------------------------------------- #
# result formatting
# --------------------------------------------------------------------------- #
def _fmt_controls(controls: List[Dict[str, Any]], limit: int = 40) -> List[str]:
    lines = []
    for control in controls[:limit]:
        name = " ".join((control.get("name") or "").split())
        if not name:
            continue
        # Document/edit controls report their whole contents as their name, which
        # can be thousands of characters. Clip so one control cannot swallow the
        # agent's context window.
        if len(name) > 90:
            name = name[:90] + "..."
        suffix = ""
        toggle = control.get("toggle")
        if toggle is not None:
            suffix += " [on]" if toggle else " [off]"
        if control.get("enabled") is False:
            suffix += " [disabled]"
        lines.append(f"- {name} ({control.get('type') or '?'}){suffix}")
    return lines


def _say(result: Dict[str, Any], ok_msg: str) -> str:
    """Turn an engine result into a short, honest string for the agent."""
    if result.get("ok"):
        message = result.get("evidence") or ok_msg
        trail = result.get("navigation") or []
        if trail:
            message += f" (via {', '.join(str(t) for t in trail)})"
        if result.get("verified") is False:
            message += " -- but I could not confirm the change took effect"
        return message

    reason = result.get("reason") or "the action failed"
    parts = [f"ERROR: {reason}"]
    options = result.get("actionable") or result.get("candidates") or []
    if options:
        shown = ", ".join(str(o) for o in options[:10] if o)
        if shown:
            parts.append(f"Controls I can see: {shown}")
    windows = result.get("windows")
    if windows and not options:
        parts.append(f"Open windows: {', '.join(str(w) for w in windows[:8])}")
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# the main tool: navigate + act
# --------------------------------------------------------------------------- #
@tool(
    name="ui_do",
    description=(
        "Do something to a control on screen, even when it is not visible yet. "
        "This is the tool to use for any request like 'turn on night light', "
        "'click Check for updates', 'change the output device', 'press Save'. "
        "It looks at the window, and if the control is not there it uses the "
        "window's own search box, opens the matching section, or scrolls -- then "
        "performs the action and confirms the result. Use action='toggle' with "
        "state for switches and checkboxes, action='type' with text for input "
        "fields, action='click' for buttons, links, menu and list entries."
    ),
    params={
        "target": {"type": "string",
                   "description": "Visible name of the control, e.g. 'Night light', 'Check for updates', 'Save'."},
        "action": {"type": "string", "enum": ["click", "toggle", "type"],
                   "description": "click a button/link, toggle a switch, or type into a field.",
                   "required": False},
        "window": {"type": "string",
                   "description": "Window title to work in, e.g. 'Settings', 'Chrome'. Leave empty for the active window.",
                   "required": False},
        "state": {"type": "boolean",
                  "description": "For action='toggle': true to switch on, false to switch off.",
                  "required": False},
        "text": {"type": "string", "description": "For action='type': the text to enter.",
                 "required": False},
    },
    category="system",
    verification={"family": "ui"},
)
def ui_do(target: str, action: str = "click", window: str = "",
          state: bool = True, text: str = "") -> str:
    action = (action or "click").strip().lower()
    if action not in ("click", "toggle", "type"):
        action = "click"
    value = text if action == "type" else state
    dbg.uia_action(f"ui_do:{action}", target, window=window,
                   result=f"state={state}" if action == "toggle" else (text[:40] if text else ""))
    result = get_navigator().reach(target, window=window or None, action=action, value=value)
    dbg.uia_action(f"ui_do:{action}:result", target, window=window,
                   result=str(result.get("evidence") or result.get("reason", "")),
                   ok=bool(result.get("ok")))
    defaults = {"click": f"Clicked '{target}'.",
                "toggle": f"Set '{target}' {'on' if state else 'off'}.",
                "type": f"Typed into '{target}'."}
    return _say(result, defaults[action])


# --------------------------------------------------------------------------- #
# direct, single-shot primitives
# --------------------------------------------------------------------------- #
@tool(
    name="ui_click",
    description=(
        "Click a control that is already visible in a window, by its exact "
        "visible name -- a button, link, menu item, list item or tab. If the "
        "control might not be on screen yet, prefer ui_do instead."
    ),
    params={
        "name": {"type": "string", "description": "Visible text/name of the control to click."},
        "window": {"type": "string", "description": "Optional window title to search inside.", "required": False},
        "control_type": {"type": "string", "description": "Optional control type, e.g. Button, MenuItem, ListItem, Hyperlink.", "required": False},
    },
    category="system",
    verification={"family": "ui"},
)
def ui_click(name: str, window: str = "", control_type: str = "") -> str:
    dbg.uia_action("click", name, window=window)
    result = get_uia_engine().click(name, window=window or None,
                                   control_type=control_type or None)
    dbg.uia_action("click_result", name, window=window,
                   result=str(result.get("evidence") or result.get("reason", "")),
                   ok=bool(result.get("ok")))
    return _say(result, f"Clicked '{name}'.")


@tool(
    name="ui_set_toggle",
    description=(
        "Turn a visible on-screen switch, toggle or checkbox on or off by name. "
        "It reads the current state first, does nothing if it already matches, "
        "and reads the control back afterwards to confirm it really changed. "
        "If the toggle may be on another page, prefer ui_do."
    ),
    params={
        "name": {"type": "string", "description": "Visible name of the toggle/switch/checkbox."},
        "on": {"type": "boolean", "description": "True to turn on, False to turn off."},
        "window": {"type": "string", "description": "Optional window title to search inside.", "required": False},
    },
    category="system",
    verification={"family": "ui"},
)
def ui_set_toggle(name: str, on: bool = True, window: str = "") -> str:
    dbg.uia_action(f"set_toggle({'on' if on else 'off'})", name, window=window)
    result = get_uia_engine().set_toggle(name, bool(on), window=window or None)
    dbg.uia_action("toggle_result", name, window=window,
                   result=str(result.get("evidence") or result.get("reason", "")),
                   ok=bool(result.get("ok")))
    return _say(result, f"Set '{name}' {'on' if on else 'off'}.")


@tool(
    name="ui_type_into",
    description=(
        "Type text into a named on-screen text field, e.g. a search box, a form "
        "field, or the 'File name' box of a save dialog. Reads the field back to "
        "confirm the text landed."
    ),
    params={
        "name": {"type": "string", "description": "Visible name/label of the text field."},
        "text": {"type": "string", "description": "Text to type into the field."},
        "window": {"type": "string", "description": "Optional window title to search inside.", "required": False},
    },
    category="system",
    verification={"family": "ui"},
)
def ui_type_into(name: str, text: str, window: str = "") -> str:
    dbg.uia_action("type_into", name, window=window, result=f"text='{str(text)[:50]}'")
    result = get_uia_engine().set_text(name, text, window=window or None)
    dbg.uia_action("type_result", name, window=window,
                   result=str(result.get("evidence") or result.get("reason", "")),
                   ok=bool(result.get("ok")))
    return _say(result, f"Typed into '{name}'.")


@tool(
    name="ui_list_controls",
    description=(
        "List the controls you can actually operate in a window -- buttons, "
        "switches, links, list entries and text fields, with their current on/off "
        "state. Use it to discover exact control names before acting, or to answer "
        "'what options are there'. Optionally filter by control type."
    ),
    params={
        "window": {"type": "string", "description": "Optional window title to inspect. Empty = the active window.", "required": False},
        "control_type": {"type": "string", "description": "Optional filter, e.g. Button, CheckBox, MenuItem, Hyperlink, Edit.", "required": False},
        "include_text": {"type": "boolean", "description": "Also include read-only text labels. Default false.", "required": False},
    },
    category="system",
    verification={"family": "query", "cacheable": False},
)
def ui_list_controls(window: str = "", control_type: str = "",
                     include_text: bool = False) -> str:
    dbg.uia_action("list_controls", window or "(active)", window=window)
    result = get_uia_engine().list_controls(
        window=window or None, control_type=control_type or None,
        include_text=bool(include_text),
    )
    if not result.get("ok"):
        dbg.uia_action("list_controls_FAIL", window or "(active)",
                       result=str(result.get("reason", "")), ok=False)
        return _say(result, "")

    controls = result.get("controls") or []
    lines = _fmt_controls(controls)
    resolved = result.get("window") or "the active window"
    dbg.uia_action("list_controls_OK", resolved,
                   result=f"{len(lines)} named of {result.get('actionable_total')} actionable",
                   ok=True)
    if not lines:
        return (f"'{resolved}' has no named controls I can operate right now. "
                "It may still be loading, or the content may be inside a page that "
                "needs scrolling.")
    header = f"Controls in '{resolved}'"
    if result.get("truncated"):
        header += f" (showing {len(lines)} of {result.get('actionable_total')} actionable)"
    return header + ":\n" + "\n".join(lines)


@tool(
    name="ui_scroll",
    description=(
        "Scroll a window so controls below the fold come into reach. Use this "
        "when ui_list_controls does not show the option you expect."
    ),
    params={
        "window": {"type": "string", "description": "Optional window title. Empty = active window.", "required": False},
        "direction": {"type": "string", "enum": ["down", "up", "top", "bottom"],
                      "description": "Scroll direction.", "required": False},
        "amount": {"type": "integer", "description": "How far to scroll (1-15, default 3).", "required": False},
    },
    category="system",
    verification={"family": "ui"},
)
def ui_scroll(window: str = "", direction: str = "down", amount: int = 3) -> str:
    result = get_uia_engine().scroll(window=window or None, direction=direction,
                                    amount=int(amount or 3))
    dbg.uia_action("scroll", window or "(active)",
                   result=str(result.get("evidence") or result.get("reason", "")),
                   ok=bool(result.get("ok")))
    return _say(result, f"Scrolled {direction}.")


@tool(
    name="ui_wait",
    description=(
        "Wait a moment for a window or page to finish loading before inspecting "
        "or clicking it. Use only when a previous step reported nothing found yet."
    ),
    params={"seconds": {"type": "number", "description": "Seconds to wait (0.5-10).", "required": False}},
    category="system",
    verification={"family": "query", "cacheable": False},
)
def ui_wait(seconds: float = 2.0) -> str:
    import time as _time
    delay = max(0.5, min(float(seconds or 2.0), 10.0))
    dbg.uia_action("wait", f"{delay:.1f}s")
    _time.sleep(delay)
    return f"Waited {delay:.1f} seconds."


@tool(
    name="ui_diagnostics",
    description=(
        "Report whether UI automation is working: the apartment state, the "
        "backend, and the list of windows that can be targeted. Use this when "
        "several UI actions fail in a row."
    ),
    params={},
    category="system",
    verification={"family": "query", "cacheable": False},
)
def ui_diagnostics() -> str:
    engine = get_uia_engine()
    status = engine.status()
    windows = engine.window_titles()
    lines = [f"UI automation: {'available' if status.get('alive') else 'NOT running'}",
             f"apartment={status.get('apartment')} generation={status.get('generation')} "
             f"retired={status.get('retired')}"]
    if status.get("backend_error"):
        lines.append(f"backend error: {status['backend_error']}")
    titles = windows.get("windows") or []
    lines.append(f"targetable windows ({len(titles)}): {', '.join(titles[:12]) or 'none'}")
    dbg.info("UIA", "DIAGNOSTICS", status)
    return "\n".join(lines)
