"""UIA Probe -- drive the UI-automation engine directly, with no LLM involved.

Why: when "click the button" fails, there are two very different causes:
  (a) the LLM never called the ui_* tool, or
  (b) the engine cannot see / cannot press the control.
This script removes the LLM entirely, so it answers (b) on its own. Everything it
does is written to data/debug_logs/trace.log exactly as a real request would be.

Run from the project root with the venv active:

    python scripts/uia_probe.py status
    python scripts/uia_probe.py windows
    python scripts/uia_probe.py list   --window "Settings"
    python scripts/uia_probe.py list   --window "Settings" --type Button
    python scripts/uia_probe.py find   --window "Settings" --name "Check for updates"
    python scripts/uia_probe.py click  --window "Settings" --name "Check for updates"
    python scripts/uia_probe.py toggle --window "Settings" --name "Night light" --on
    python scripts/uia_probe.py type   --window "Notepad" --name "Text editor" --text "hello"
    python scripts/uia_probe.py scroll --window "Settings" --direction down
    python scripts/uia_probe.py reach  --name "Night light" --window "Settings" --action toggle
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.agent.automation.navigator import get_navigator  # noqa: E402
from app.services.agent.automation.uia_engine import get_uia_engine  # noqa: E402
from app.services.debug_logger import dbg, foreground_window_title  # noqa: E402


def _dump(label: str, result: dict) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(result, indent=2, default=str, ensure_ascii=False)[:6000])


def _print_controls(controls) -> None:
    for control in controls:
        toggle = control.get("toggle")
        mark = "" if toggle is None else (" [on]" if toggle else " [off]")
        disabled = "" if control.get("enabled") is not False else " [disabled]"
        path = control.get("path") or ""
        suffix = f"   <- {path}" if path else ""
        print(f"  {str(control.get('type') or '?'):<14} "
              f"{control.get('name') or '<no name>'}{mark}{disabled}{suffix}")


def cmd_status(_args) -> None:
    engine = get_uia_engine()
    _dump("status", engine.status())
    print(f"\nForeground window: {foreground_window_title() or '<unknown>'}")
    print(f"Engine available : {engine.available()}")


def cmd_windows(_args) -> None:
    result = get_uia_engine().window_titles()
    print(f"\nForeground window: {foreground_window_title() or '<unknown>'}\n")
    if not result.get("ok"):
        _dump("windows", result)
        return
    print("Top-level windows (use any part of a title as --window):\n")
    for title in result.get("windows") or []:
        print(f"  {title}")


def cmd_list(args) -> None:
    t0 = time.perf_counter()
    result = get_uia_engine().list_controls(
        window=args.window or None, control_type=args.type or None,
        limit=args.limit, include_text=args.text_too,
    )
    took = time.perf_counter() - t0
    controls = result.pop("controls", [])
    _dump("list_controls", result)
    print(f"\n{len(controls)} control(s) returned in {took:.2f}s\n")
    _print_controls(controls)


def cmd_find(args) -> None:
    _dump("find", get_uia_engine().find(args.name, window=args.window or None,
                                       control_type=args.type or None))


def cmd_click(args) -> None:
    print(f"Foreground before: {foreground_window_title()}")
    _dump("click", get_uia_engine().click(args.name, window=args.window or None,
                                         control_type=args.type or None))
    time.sleep(0.6)
    print(f"Foreground after : {foreground_window_title()}")


def cmd_toggle(args) -> None:
    _dump("set_toggle", get_uia_engine().set_toggle(args.name, bool(args.on),
                                                   window=args.window or None))


def cmd_type(args) -> None:
    _dump("set_text", get_uia_engine().set_text(args.name, args.text,
                                               window=args.window or None))


def cmd_scroll(args) -> None:
    _dump("scroll", get_uia_engine().scroll(window=args.window or None,
                                           direction=args.direction, amount=args.limit))


def cmd_reach(args) -> None:
    """Exercise the full navigation loop: search / drill-in / scroll, then act."""
    value = args.text if args.action == "type" else bool(args.on)
    _dump("reach", get_navigator().reach(args.name, window=args.window or None,
                                        action=args.action, value=value))


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe the J.A.R.V.I.S UIA engine directly.")
    parser.add_argument("action", choices=["status", "windows", "list", "find", "click",
                                          "toggle", "type", "scroll", "reach"])
    parser.add_argument("--window", default="", help="Part of the target window title.")
    parser.add_argument("--name", default="", help="Visible name of the control.")
    parser.add_argument("--type", default="", help="Control type filter, e.g. Button, CheckBox.")
    parser.add_argument("--text", default="", help="Text to type (for 'type'/'reach').")
    parser.add_argument("--limit", type=int, default=200, help="Max controls / scroll amount.")
    parser.add_argument("--on", action="store_true", help="Turn the toggle ON (default OFF).")
    parser.add_argument("--direction", default="down", help="Scroll direction.")
    parser.add_argument("--text-too", action="store_true",
                        help="Include read-only text labels in listings.")
    parser.add_argument("--action", dest="action_kind", default="click",
                        choices=["click", "toggle", "type"],
                        help="For 'reach': what to do once the control is found.")
    args = parser.parse_args()
    # argparse cannot have two dests named "action"; map it back for cmd_reach.
    args.action, args.action_kind = args.action, args.action_kind
    setattr(args, "action_name", args.action)

    dbg.session_start(session_id="uia-probe",
                      user_message=f"probe {args.action} {args.window} {args.name}".strip())
    print(f"\nTrace log: data/debug_logs/trace.log")

    handlers = {
        "status": cmd_status, "windows": cmd_windows, "list": cmd_list,
        "find": cmd_find, "click": cmd_click, "toggle": cmd_toggle,
        "type": cmd_type, "scroll": cmd_scroll, "reach": cmd_reach,
    }
    handler = handlers[args.action]
    if handler is cmd_reach:
        args.action = args.action_kind  # cmd_reach reads args.action as the verb
    try:
        handler(args)
    finally:
        dbg.session_end(final_response=f"probe {args.action_name} finished")
        print("\nFull diagnostics appended to data/debug_logs/trace.log\n")


if __name__ == "__main__":
    main()
