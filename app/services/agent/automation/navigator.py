"""Generic UI navigation -- reach a control that is not on screen yet.

THE PROBLEM THIS SOLVES
-----------------------
Clicking a control the agent can already see is the easy half. The hard half is
"turn on night light": the toggle exists, but it is two pages deep inside
Settings and nothing in the current window is named "night light".

The old approach was a per-feature macro (`check_for_updates` hardcoded four
button labels and gave up). That does not generalise: every new request needs new
code, and the hardcoded labels break on other Windows builds and locales.

THE APPROACH
------------
One loop, no application knowledge:

    read the window
      -> is the target here? act on it, verify, done.
      -> is there a search box? type the target into it, read again.
      -> is there a navigation item that looks like a route to the target?
         click it, read again.
      -> is there more window below the fold? scroll, read again.
      -> out of steps? report honestly what WAS on screen.

Two generic mechanisms carry most of Windows:

* **Search.** Settings, Explorer, Control Panel, the Start menu and most modern
  apps have a search field. Typing the goal into it collapses arbitrary
  navigation depth into one step, without knowing any route in advance.
* **Scroll and re-scan.** A control below the fold is in the tree but usually not
  operable, so scrolling is a real navigation step rather than a cosmetic one.

Everything here is driven by the live tree, so it works the same in Settings, a
browser, a file dialog, or an app nobody has ever tested against.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import config as _cfg
from app.services.debug_logger import dbg

logger = logging.getLogger("J.A.R.V.I.S")

_MAX_STEPS = int(getattr(_cfg, "UIA_NAV_MAX_STEPS", 6))

# Names that mean "type here to find things". Matched as substrings, lower-case.
_SEARCH_HINTS = ("search", "find a setting", "find setting", "query", "khoj")
# Control types that behave like a navigation entry.
_NAV_TYPES = ("listitem", "treeitem", "hyperlink", "tabitem", "menuitem", "button")


def _norm(s: Any) -> str:
    return str(s or "").strip().lower()


def _tokens(text: str) -> List[str]:
    import re
    return [w for w in re.split(r"[\s\-_/.,:()]+", _norm(text)) if len(w) >= 3]


def _looks_like_search(control: Dict[str, Any]) -> bool:
    if _norm(control.get("type")) not in ("edit", "combobox"):
        return False
    haystack = f"{_norm(control.get('name'))} {_norm(control.get('path'))}"
    return any(hint in haystack for hint in _SEARCH_HINTS)


def _route_score(control: Dict[str, Any], goal_tokens: List[str]) -> float:
    """How likely this control leads *towards* the goal.

    Deliberately simple and explainable: shared words between the control's name
    (or its path) and the goal. A "Display" row scores for "night light" only if
    the words overlap, so the loop does not wander randomly.
    """
    if _norm(control.get("type")) not in _NAV_TYPES:
        return 0.0
    if not goal_tokens:
        return 0.0
    haystack = set(_tokens(f"{control.get('name')} {control.get('path')}"))
    if not haystack:
        return 0.0
    hits = sum(1 for token in goal_tokens if token in haystack)
    return round(hits / len(goal_tokens), 3)


class Navigator:
    """Drives the read -> act -> re-read loop over a live window."""

    def __init__(self, engine=None, max_steps: Optional[int] = None) -> None:
        self._engine = engine
        self.max_steps = int(max_steps or _MAX_STEPS)

    @property
    def engine(self):
        if self._engine is None:
            from app.services.agent.automation.uia_engine import get_uia_engine
            self._engine = get_uia_engine()
        return self._engine

    # ------------------------------------------------------------------ #
    def reach(self, target: str, window: Optional[str] = None,
              action: str = "click", value: Any = None) -> Dict[str, Any]:
        """Find `target` -- searching, navigating and scrolling as needed -- then act.

        action: "click" | "toggle" | "type" | "find"
        value:  desired state for toggle, or the text for type.
        """
        engine = self.engine
        target = (target or "").strip()
        if not target:
            return {"ok": False, "reason": "no target control was given"}

        goal_tokens = _tokens(target)
        trail: List[str] = []
        tried_search = False
        scrolled = 0
        current_window = window

        for step in range(1, self.max_steps + 1):
            attempt = self._act(engine, target, current_window, action, value)
            if attempt.get("ok"):
                attempt["navigation"] = trail
                attempt["steps"] = step
                dbg.uia_diag("nav.success", target=target, step=step,
                             trail=" -> ".join(trail) or "direct")
                return attempt

            snapshot = engine.list_controls(window=current_window, limit=80)
            if not snapshot.get("ok"):
                snapshot["navigation"] = trail
                return snapshot
            controls = snapshot.get("controls") or []
            # Stay on the window we actually resolved; the caller's hint may have
            # been a page name rather than a window title.
            current_window = snapshot.get("window") or current_window
            dbg.uia_diag("nav.scan", step=step, target=target,
                         window=str(current_window)[:50],
                         controls=len(controls),
                         actionable=snapshot.get("actionable_total"))

            move = self._choose_move(controls, goal_tokens, tried_search, scrolled)
            if move is None:
                return self._give_up(target, action, current_window, controls, trail)

            if move["kind"] == "search":
                tried_search = True
                typed = engine.set_text(move["name"], target, window=current_window)
                trail.append(f"searched '{target}'")
                dbg.uia_diag("nav.search", field=move["name"], query=target,
                             ok=typed.get("ok"))
                if not typed.get("ok"):
                    continue
                self._submit_search(engine, move["name"], current_window)
                time.sleep(1.0)
                continue

            if move["kind"] == "navigate":
                clicked = engine.click(move["name"], window=current_window)
                trail.append(f"opened '{move['name']}'")
                dbg.uia_diag("nav.navigate", into=move["name"],
                             score=move["score"], ok=clicked.get("ok"))
                if clicked.get("ok"):
                    time.sleep(0.9)
                continue

            if move["kind"] == "scroll":
                scrolled += 1
                engine.scroll(window=current_window, direction="down", amount=3)
                trail.append("scrolled down")
                time.sleep(0.4)
                continue

        snapshot = engine.list_controls(window=current_window, limit=80)
        return self._give_up(target, action, current_window,
                             snapshot.get("controls") or [], trail)

    # ------------------------------------------------------------------ #
    def _act(self, engine, target: str, window: Optional[str],
             action: str, value: Any) -> Dict[str, Any]:
        if action == "toggle":
            return engine.set_toggle(target, bool(value), window=window)
        if action == "type":
            return engine.set_text(target, str(value or ""), window=window)
        if action == "find":
            return engine.find(target, window=window)
        return engine.click(target, window=window)

    def _choose_move(self, controls: List[Dict[str, Any]], goal_tokens: List[str],
                     tried_search: bool, scrolled: int) -> Optional[Dict[str, Any]]:
        """Pick the next navigation step. Search first: it is the shortest route."""
        if not tried_search:
            for control in controls:
                if _looks_like_search(control):
                    return {"kind": "search", "name": control.get("name") or "Search"}

        best, best_score = None, 0.0
        for control in controls:
            score = _route_score(control, goal_tokens)
            if score > best_score:
                best, best_score = control, score
        if best is not None and best_score >= 0.34:
            return {"kind": "navigate", "name": best.get("name"), "score": best_score}

        if scrolled < 2:
            return {"kind": "scroll"}
        return None

    @staticmethod
    def _submit_search(engine, field_name: str, window: Optional[str]) -> None:
        """Commit a search. Most search fields filter live; Enter helps the rest."""
        try:
            from app.services.agent.tools.desktop_tools import press_hotkey
            press_hotkey("enter")
        except Exception as exc:  # noqa: BLE001
            logger.debug("[NAV] search submit skipped: %s", exc)

    @staticmethod
    def _give_up(target: str, action: str, window: Optional[str],
                 controls: List[Dict[str, Any]], trail: List[str]) -> Dict[str, Any]:
        """Report failure with the evidence the agent needs to try something else."""
        visible = [f"{c.get('name')} ({c.get('type')})"
                   for c in controls if (c.get("name") or "").strip()][:20]
        dbg.uia_diag("nav.exhausted", target=target, window=str(window)[:50],
                     trail=" -> ".join(trail) or "none", visible=len(visible))
        return {
            "ok": False, "action": action, "navigation": trail,
            "window": window,
            "reason": (f"could not reach '{target}' in '{window}' after "
                       f"{len(trail) or 0} navigation step(s)"),
            "actionable": visible,
        }


_navigator: Optional[Navigator] = None


def get_navigator() -> Navigator:
    global _navigator
    if _navigator is None:
        _navigator = Navigator()
    return _navigator
