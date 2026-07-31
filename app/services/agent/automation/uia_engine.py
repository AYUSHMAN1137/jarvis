"""UIA engine -- find and drive real Windows UI controls.

WHAT THIS IS
------------
A generic Windows UI Automation driver. It walks the UIA tree, finds a control
by its visible NAME (and optionally TYPE), and invokes / toggles / types into
it. The same code path clicks a YouTube play button, a Settings switch, and a
Notepad menu item. There is no per-application logic anywhere in this file.

THREADING CONTRACT (the important part)
--------------------------------------
UIA is COM. A COM object may only be used from the apartment that created it;
calling it from another thread makes COM marshal the call and wait for the
owning thread to service it, which deadlocks when the owner is an asyncio loop.
That bug made every UI action in production fail with zero controls scored.

Therefore: **every COM touch happens inside `ui_thread`, and no COM object ever
leaves it.** Each public method is one self-contained job -- resolve the window,
walk the tree, score candidates, act, read the result back -- and returns plain
Python data. Callers never hold a live element.

DESIGN RULES (locked)
---------------------
* Reliability first. Every call is fail-soft: a missing package, a control that
  isn't there, or a wedged app returns an honest {"ok": False, "reason": ...}.
  Nothing raises. Nothing fakes success.
* No hardcode. Controls are matched by name/type/actionability, never by fixed
  coordinates or app-specific magic.
* Actionability matters as much as the name. A Text label that merely *reads*
  "Bluetooth" must never beat the CheckBox that actually toggles it.
* Everything is observable: window resolution, tree size, candidate scores and
  the technique used to act all go to the session debug log.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import config as _cfg
from app.services.agent.automation.ui_thread import get_ui_thread
from app.services.debug_logger import dbg, foreground_window_title

logger = logging.getLogger("J.A.R.V.I.S")

_ENABLED = bool(getattr(_cfg, "UIA_ENABLED", True))
_LIBRARY = str(getattr(_cfg, "UIA_LIBRARY", "pywinauto")).lower()
# Budget for a whole operation (resolve window + walk tree + act), in seconds.
_OP_TIMEOUT = float(getattr(_cfg, "UIA_OP_TIMEOUT", 20.0))
# Budget for waiting on a window to appear.
_WINDOW_WAIT = float(getattr(_cfg, "UIA_WINDOW_WAIT", 3.0))
# Hard cap on how many tree nodes we will inspect, so a pathological app cannot
# stall the apartment. Chrome/Edge content trees can be enormous.
_MAX_NODES = int(getattr(_cfg, "UIA_MAX_NODES", 4000))

# Control types a user can actually operate. Anything else is decoration or
# structure: useful as context, never as a click target.
ACTIONABLE_TYPES = frozenset({
    "button", "checkbox", "radiobutton", "combobox", "edit", "hyperlink",
    "listitem", "menuitem", "tabitem", "treeitem", "slider", "splitbutton",
    "spinner", "thumb", "custom", "datagrid", "document",
})
# Types that hold text worth reading but are not click targets.
TEXT_TYPES = frozenset({"text", "statictext", "image", "group", "pane", "list", "tab"})
# Types that can receive typed input.
EDITABLE_TYPES = frozenset({"edit", "combobox", "document", "custom"})
# Types that carry an on/off state.
TOGGLE_TYPES = frozenset({"checkbox", "radiobutton", "menuitem", "button", "custom"})


# --------------------------------------------------------------------------- #
# text helpers (pure -- unit testable without Windows)
# --------------------------------------------------------------------------- #
def _clean(s: Any, limit: int = 80) -> str:
    """Collapse whitespace + clip -- safe, leak-free log lines."""
    return " ".join(str(s or "").split())[:limit]


def _norm(s: Any) -> str:
    return str(s or "").strip().lower()


def _tokens(s: str) -> List[str]:
    return [w for w in re.split(r"[\s\-_/.,:()\[\]]+", _norm(s)) if len(w) >= 2]


def match_score(target: str, candidate: str) -> float:
    """How well `candidate` (a control name) matches the wanted `target`.

    Pure + deterministic so it is fully unit-testable:
      1.0  exact (normalized) match
      0.9  target is a substring of candidate (or vice-versa)
      <0.9 fraction of target tokens found in the candidate
      0.0  nothing in common
    """
    t, c = _norm(target), _norm(candidate)
    if not t or not c:
        return 0.0
    if t == c:
        return 1.0
    if t in c or c in t:
        return 0.9
    ttoks = _tokens(t)
    if not ttoks:
        return 0.0
    ctoks = set(_tokens(c))
    hit = sum(1 for w in ttoks if w in ctoks)
    return round(hit / len(ttoks), 3) if hit else 0.0


def is_actionable(control_type: Any) -> bool:
    return _norm(control_type) in ACTIONABLE_TYPES


def action_fit(control_type: Any, action: str) -> float:
    """How suitable a control type is for the requested action.

    This is what stops the engine clicking a text label that happens to have the
    right words on it. Returns a multiplier applied to the name score, so a
    perfectly-named but unusable control loses to a slightly-worse usable one.
    """
    ct = _norm(control_type)
    if action == "type":
        if ct in EDITABLE_TYPES:
            return 1.0
        return 0.25
    if action == "toggle":
        if ct in ("checkbox", "radiobutton"):
            return 1.0
        if ct in TOGGLE_TYPES:
            return 0.85
        return 0.25
    # click
    if ct in ("button", "hyperlink", "menuitem", "listitem", "tabitem",
              "treeitem", "splitbutton", "checkbox", "radiobutton"):
        return 1.0
    if ct in ACTIONABLE_TYPES:
        return 0.8
    return 0.3


# --------------------------------------------------------------------------- #
# engine
# --------------------------------------------------------------------------- #
class UIAEngine:
    """Generic Windows UI Automation driver. All COM work runs on the UI thread."""

    # Minimum combined score to accept a control as "the one".
    MIN_SCORE = 0.5

    def __init__(self, backend_factory: Optional[Callable[[], Any]] = None,
                 enabled: Optional[bool] = None,
                 op_timeout: Optional[float] = None) -> None:
        self._backend_factory = backend_factory
        self._enabled = _ENABLED if enabled is None else bool(enabled)
        self._op_timeout = float(op_timeout if op_timeout is not None else _OP_TIMEOUT)
        self._backend: Any = None
        self._backend_generation = -1
        self._backend_error: str = ""
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ #
    # backend lifecycle (always constructed INSIDE the UI apartment)
    # ------------------------------------------------------------------ #
    def _backend_in_apartment(self):
        """Return the backend, building it if this apartment doesn't have one.

        MUST be called from the UI thread. The generation check matters: when a
        wedged apartment is retired its COM objects die with it, so the
        replacement apartment has to build its own.
        """
        generation = get_ui_thread().generation
        if self._backend is not None and self._backend_generation == generation:
            return self._backend
        factory = self._backend_factory or _PywinautoBackend
        try:
            self._backend = factory()
            self._backend_generation = generation
            self._backend_error = ""
            logger.info("[UIA] backend ready: %s (apartment gen=%d)", _LIBRARY, generation)
            dbg.uia_diag("backend.ready", library=_LIBRARY, generation=generation,
                         apartment=get_ui_thread().apartment,
                         op_timeout=self._op_timeout, max_nodes=_MAX_NODES)
        except Exception as exc:  # noqa: BLE001
            self._backend = None
            self._backend_generation = -1
            self._backend_error = _clean(exc, 160)
            logger.warning("[UIA] backend unavailable (%s): %s", _LIBRARY, self._backend_error)
            dbg.error("UIA", f"backend unavailable ({_LIBRARY})",
                      {"library": _LIBRARY, "generation": generation}, exc=exc)
        return self._backend

    def _call(self, label: str, fn: Callable[[Any], Any],
              timeout: Optional[float] = None) -> Dict[str, Any]:
        """Run `fn(backend)` on the UI apartment; normalise every failure mode."""
        budget = float(timeout if timeout is not None else self._op_timeout)

        def _job():
            backend = self._backend_in_apartment()
            if backend is None:
                return {"ok": False, "action": label, "available": False,
                        "reason": self._unavailable_reason()}
            return fn(backend)

        result = get_ui_thread().submit(_job, timeout=budget, label=f"uia.{label}")

        if result.timed_out:
            dbg.uia_diag(f"{label}.TIMEOUT", timeout_s=budget,
                         elapsed_ms=round(result.elapsed_ms))
            logger.warning("[UIA] %s timed out after %.1fs", label, budget)
            return {"ok": False, "action": label, "timed_out": True,
                    "reason": (f"the UI did not respond within {budget:.0f}s. The window "
                               "may be busy or frozen; try again or use a different window.")}
        if result.error is not None:
            dbg.error("UIA", f"{label} raised",
                      {"elapsed_ms": round(result.elapsed_ms)}, exc=result.error)
            return {"ok": False, "action": label,
                    "reason": f"{label} failed: {_clean(result.error, 140)}"}
        out = result.value if isinstance(result.value, dict) else {"ok": False, "action": label,
                                                                  "reason": "no result"}
        out.setdefault("elapsed_ms", round(result.elapsed_ms))
        return out

    def _unavailable_reason(self) -> str:
        if not self._enabled:
            return "UI automation is disabled in configuration (UIA_ENABLED=False)."
        detail = f" ({self._backend_error})" if self._backend_error else ""
        return ("UI automation is unavailable -- the 'pywinauto' package isn't installed "
                f"(pip install pywinauto) or this isn't Windows{detail}.")

    def warmup(self, timeout: float = 45.0) -> Dict[str, Any]:
        """Build the backend up front, at startup.

        Importing pywinauto and constructing the UIA Desktop is a one-off cost of
        several seconds. Paying it inside the first user request made that request
        look like a hang, so startup pays it instead.
        """
        started = time.perf_counter()
        result = self._call("warmup", lambda b: {"ok": True, "action": "warmup"},
                            timeout=timeout)
        elapsed = time.perf_counter() - started
        if result.get("ok"):
            logger.info("[UIA] warmed up in %.2fs (apartment=%s)",
                        elapsed, get_ui_thread().apartment)
        else:
            logger.warning("[UIA] warmup failed after %.2fs: %s",
                           elapsed, result.get("reason", ""))
        dbg.uia_diag("warmup", ok=bool(result.get("ok")),
                     elapsed_ms=round(elapsed * 1000),
                     reason=result.get("reason", ""))
        return result

    def available(self) -> bool:
        """True only if enabled AND a backend really loaded in a live apartment.

        Reading the cached backend reference is a plain attribute read -- no COM
        call -- so this stays cheap once warmed up and never blocks a request.
        """
        if not self._enabled:
            return False
        if self._backend is not None and self._backend_generation == get_ui_thread().generation:
            return True
        return bool(self._call("probe", lambda b: {"ok": True, "action": "probe"}).get("ok"))

    def status(self) -> Dict[str, Any]:
        st = get_ui_thread().status()
        st.update({"enabled": self._enabled, "library": _LIBRARY,
                   "backend_error": self._backend_error})
        return st

    # ------------------------------------------------------------------ #
    # perception
    # ------------------------------------------------------------------ #
    def window_titles(self) -> Dict[str, Any]:
        """Titles of the top-level windows we could target."""
        return self._call("windows", lambda b: {
            "ok": True, "action": "windows", "windows": b.window_titles(),
        }, timeout=10.0)

    def list_controls(self, window: Optional[str] = None,
                      control_type: Optional[str] = None,
                      limit: int = 60,
                      include_text: bool = False) -> Dict[str, Any]:
        """Snapshot the controls in a window.

        Returns *actionable* controls by default. The old behaviour returned the
        first N nodes in raw tree order, which in practice meant unnamed layout
        panes -- the agent received a list with nothing clickable in it.
        """
        dbg.uia_diag("list.begin", window=window or "<active>",
                     control_type=control_type or "<any>", limit=limit,
                     include_text=include_text,
                     foreground=foreground_window_title()[:70])

        def _job(backend):
            win, nodes = backend.walk_best(window)
            if win.wrapper is None:
                return self._window_missing(backend, "list", window)
            picked = _select_controls(nodes, control_type, include_text, limit)
            dbg.uia_diag("list.enumerated", requested=window or "<active>",
                         resolved=_clean(win.title, 60), strategy=win.strategy,
                         nodes=len(nodes), returned=len(picked["controls"]),
                         actionable_total=picked["actionable_total"],
                         truncated=picked["truncated"])
            dbg.uia_tree(win.title or "<active>", picked["controls"])
            return {
                "ok": True, "action": "list", "window": win.title,
                "window_strategy": win.strategy,
                "count": len(picked["controls"]),
                "actionable_total": picked["actionable_total"],
                "nodes": len(nodes), "truncated": picked["truncated"],
                "controls": picked["controls"],
            }

        return self._call("list", _job)

    def find(self, name: str, window: Optional[str] = None,
             control_type: Optional[str] = None,
             action: str = "click") -> Dict[str, Any]:
        """Locate a control without touching it."""
        def _job(backend):
            found = self._locate(backend, name, window, control_type, action)
            if found["ctrl"] is None:
                return found["failure"]
            info = found["info"]
            return {"ok": True, "action": "find", "score": found["score"],
                    "name": info["name"], "type": info["type"],
                    "path": info["path"], "toggle": info["toggle"],
                    "enabled": info["enabled"], "window": found["window"]}
        return self._call("find", _job)

    # ------------------------------------------------------------------ #
    # actions
    # ------------------------------------------------------------------ #
    def click(self, name: str, window: Optional[str] = None,
              control_type: Optional[str] = None) -> Dict[str, Any]:
        def _job(backend):
            found = self._locate(backend, name, window, control_type, "click")
            if found["ctrl"] is None:
                return found["failure"]
            ctrl, info = found["ctrl"], found["info"]
            before = foreground_window_title()
            t0 = time.perf_counter()
            try:
                method = ctrl.invoke()
            except Exception as exc:  # noqa: BLE001
                dbg.error("UIA", f"click '{_clean(name)}' failed",
                          {"control": info["name"], "type": info["type"]}, exc=exc)
                return {"ok": False, "action": "click",
                        "reason": f"found '{info['name']}' but could not press it: {_clean(exc, 100)}",
                        "candidates": found["candidate_names"]}
            after_state = ctrl.snapshot()
            dbg.uia_diag("click.invoked", target=name, method=method,
                         control=_clean(info["name"], 60), type=info["type"],
                         score=found["score"],
                         elapsed_ms=round((time.perf_counter() - t0) * 1000),
                         foreground_before=before[:50],
                         foreground_after=foreground_window_title()[:50])
            return {"ok": True, "action": "click", "method": method,
                    "evidence": f"clicked '{_clean(info['name'], 60)}' ({info['type']})",
                    "name": info["name"], "type": info["type"],
                    "path": info["path"], "score": found["score"],
                    "state_after": after_state.get("toggle"),
                    "window": found["window"]}
        return self._call("click", _job)

    def set_toggle(self, name: str, on: bool, window: Optional[str] = None,
                   control_type: Optional[str] = None) -> Dict[str, Any]:
        def _job(backend):
            found = self._locate(backend, name, window, control_type, "toggle")
            if found["ctrl"] is None:
                return found["failure"]
            ctrl, info = found["ctrl"], found["info"]
            current = info["toggle"]
            dbg.uia_diag("toggle.state", target=name, current=current, wanted=bool(on),
                         control=_clean(info["name"], 60), type=info["type"])

            # Not every switch exposes a toggle state. Windows 11 renders Night
            # light as a plain Button, and the real affordance is a separate
            # button labelled "Turn off now" / "Turn on now". Those labels state
            # the transition in words, which is generic vocabulary rather than
            # app-specific knowledge -- and the label flipping afterwards is
            # itself the proof the change landed.
            if current is None:
                switched = self._toggle_via_state_button(
                    backend, found.get("nodes") or [], name, bool(on), found["window"]
                )
                if switched is not None:
                    return switched

            if current is not None and bool(current) == bool(on):
                return {"ok": True, "action": "toggle", "changed": False,
                        "evidence": f"'{_clean(info['name'], 60)}' is already "
                                    f"{'on' if on else 'off'}",
                        "state": bool(on), "verified": True,
                        "name": info["name"], "window": found["window"]}
            t0 = time.perf_counter()
            try:
                method = ctrl.set_toggle(bool(on))
            except Exception as exc:  # noqa: BLE001
                dbg.error("UIA", f"toggle '{_clean(name)}' failed",
                          {"control": info["name"]}, exc=exc)
                return {"ok": False, "action": "toggle",
                        "reason": f"found '{info['name']}' but could not switch it: {_clean(exc, 100)}"}
            # Read the control back: this is the only honest proof it flipped.
            state_after = ctrl.settle_toggle(bool(on))
            verified = state_after is not None and bool(state_after) == bool(on)
            dbg.uia_diag("toggle.done", target=name, method=method,
                         state_after=state_after, verified=verified,
                         elapsed_ms=round((time.perf_counter() - t0) * 1000))
            if state_after is not None and not verified:
                return {"ok": False, "action": "toggle",
                        "reason": (f"pressed '{_clean(info['name'], 60)}' but it is still "
                                   f"{'on' if state_after else 'off'}"),
                        "state": bool(state_after), "verified": False}
            return {"ok": True, "action": "toggle", "changed": True, "method": method,
                    "evidence": f"set '{_clean(info['name'], 60)}' -> {'on' if on else 'off'}",
                    "state": bool(on), "verified": verified,
                    "name": info["name"], "window": found["window"]}
        return self._call("toggle", _job)

    def set_text(self, name: str, text: str, window: Optional[str] = None,
                 control_type: Optional[str] = None) -> Dict[str, Any]:
        def _job(backend):
            found = self._locate(backend, name, window, control_type or "", "type")
            if found["ctrl"] is None:
                return found["failure"]
            ctrl, info = found["ctrl"], found["info"]
            t0 = time.perf_counter()
            try:
                method = ctrl.set_text(str(text))
            except Exception as exc:  # noqa: BLE001
                dbg.error("UIA", f"set_text '{_clean(name)}' failed",
                          {"control": info["name"]}, exc=exc)
                return {"ok": False, "action": "set_text",
                        "reason": f"found '{info['name']}' but could not type into it: {_clean(exc, 100)}"}
            value = ctrl.text_value()
            verified = value is not None and _norm(text) in _norm(value)
            dbg.uia_diag("set_text.done", target=name, method=method, chars=len(str(text)),
                         value_after=_clean(value, 60), verified=verified,
                         elapsed_ms=round((time.perf_counter() - t0) * 1000))
            return {"ok": True, "action": "set_text", "method": method,
                    "evidence": f"typed into '{_clean(info['name'], 60)}'",
                    "verified": verified, "value": value,
                    "name": info["name"], "window": found["window"]}
        return self._call("set_text", _job)

    def read_state(self, name: str, window: Optional[str] = None) -> Dict[str, Any]:
        """Read a control's current on/off state -- used to verify actions."""
        def _job(backend):
            found = self._locate(backend, name, window, None, "toggle")
            if found["ctrl"] is None:
                return found["failure"]
            info = found["info"]
            return {"ok": True, "action": "read_state", "name": info["name"],
                    "type": info["type"], "toggle": info["toggle"],
                    "enabled": info["enabled"], "window": found["window"]}
        return self._call("read_state", _job)

    def read_switch_state(self, window: Optional[str] = None) -> Dict[str, Any]:
        """Infer a setting's current state from its transition button.

        A window offering "Turn off now" is currently ON; one offering "Turn on
        now" is currently OFF. This gives an honest reading for switches that
        expose no toggle property at all -- which is how Windows 11 renders
        several of them, and previously left every such action UNVERIFIED.
        """
        def _job(backend):
            _win, nodes = backend.walk_best(window or None)
            if not nodes:
                return {"ok": False, "action": "read_switch_state",
                        "reason": "window has no readable controls"}
            if _find_state_button(nodes, on=False) is not None:
                return {"ok": True, "action": "read_switch_state", "state": True,
                        "evidence": "an 'off' action is offered, so it is on"}
            if _find_state_button(nodes, on=True) is not None:
                return {"ok": True, "action": "read_switch_state", "state": False,
                        "evidence": "an 'on' action is offered, so it is off"}
            return {"ok": False, "action": "read_switch_state",
                    "reason": "no on/off action visible in this window"}
        return self._call("read_switch_state", _job)

    def scroll(self, window: Optional[str] = None, direction: str = "down",
               amount: int = 3) -> Dict[str, Any]:
        """Scroll a window so off-screen controls come into reach.

        Controls below the fold are still in the UIA tree but are frequently not
        interactable until scrolled, so this is a first-class navigation step.
        """
        def _job(backend):
            win = backend.resolve_window(window)
            if win.wrapper is None:
                return self._window_missing(backend, "scroll", window)
            moved = backend.scroll(win.wrapper, direction, int(amount))
            dbg.uia_diag("scroll", window=_clean(win.title, 60),
                         direction=direction, amount=amount, method=moved)
            return {"ok": bool(moved), "action": "scroll", "window": win.title,
                    "method": moved,
                    "evidence": f"scrolled {direction} in '{_clean(win.title, 50)}'",
                    "reason": "" if moved else "this window does not scroll"}
        return self._call("scroll", _job)

    # ------------------------------------------------------------------ #
    # toggles expressed as a labelled button
    # ------------------------------------------------------------------ #
    def _toggle_via_state_button(self, backend, nodes: List[Dict[str, Any]],
                                 target: str, on: bool,
                                 window: str) -> Optional[Dict[str, Any]]:
        """Flip a state using a button that names the transition.

        Returns a result dict when it handled the request, or None to let the
        caller fall back to the ordinary toggle path.
        """
        wanted = _find_state_button(nodes, on)
        opposite_before = _find_state_button(nodes, not on)

        if wanted is None:
            # No button offers the wanted transition. If the opposite one is
            # present, the setting is ALREADY in the state the user asked for --
            # "Turn on now" showing means it is currently off.
            if opposite_before is not None:
                evidence = (f"'{_clean(target, 50)}' is already "
                            f"{'on' if on else 'off'}")
                dbg.uia_diag("toggle.already_in_state", target=target,
                             wanted="on" if on else "off",
                             evidence_control=_clean(opposite_before[1]["name"], 40))
                return {"ok": True, "action": "toggle", "changed": False,
                        "evidence": evidence, "state": bool(on), "verified": True,
                        "name": target, "window": window}
            return None

        ctrl, info = wanted
        dbg.uia_diag("toggle.state_button", target=target,
                     using=_clean(info["name"], 50), type=info["type"],
                     wanted="on" if on else "off")
        t0 = time.perf_counter()
        try:
            method = ctrl.invoke()
        except Exception as exc:  # noqa: BLE001
            dbg.error("UIA", f"state button for '{_clean(target)}' failed",
                      {"control": info["name"]}, exc=exc)
            return {"ok": False, "action": "toggle",
                    "reason": (f"found '{info['name']}' but could not press it: "
                               f"{_clean(exc, 100)}")}

        # Proof: the button should now offer the OPPOSITE transition.
        verified, label_after = self._confirm_state_flip(backend, window, not on)
        dbg.uia_diag("toggle.state_button_done", target=target, method=method,
                     verified=verified, label_after=_clean(label_after, 50),
                     elapsed_ms=round((time.perf_counter() - t0) * 1000))
        if verified:
            return {"ok": True, "action": "toggle", "changed": True, "method": method,
                    "evidence": (f"pressed '{_clean(info['name'], 40)}' and "
                                 f"'{_clean(target, 40)}' is now {'on' if on else 'off'}"),
                    "state": bool(on), "verified": True,
                    "name": info["name"], "window": window}
        return {"ok": True, "action": "toggle", "changed": True, "method": method,
                "evidence": f"pressed '{_clean(info['name'], 40)}'",
                "state": bool(on), "verified": False,
                "name": info["name"], "window": window}

    @staticmethod
    def _confirm_state_flip(backend, window: str,
                            expect_opposite_on: bool) -> Tuple[bool, str]:
        """Re-read the window and look for the opposite-transition button.

        Runs inline on the backend we were already given. It must NOT dispatch a
        new job: we are already inside the UI apartment, and the only thread that
        could service that job is this one.
        """
        for _ in range(6):
            try:
                _win, nodes = backend.walk_best(window or None)
            except Exception:  # noqa: BLE001
                nodes = []
            found = _find_state_button(nodes, expect_opposite_on)
            if found is not None:
                return True, str(found[1]["name"])
            time.sleep(0.4)
        return False, ""

    # ------------------------------------------------------------------ #
    # matching (runs inside the apartment)
    # ------------------------------------------------------------------ #
    def _locate(self, backend, name: str, window: Optional[str],
                control_type: Optional[str], action: str) -> Dict[str, Any]:
        """Find the best control for `action`. Returns ctrl/info or a failure dict."""
        dbg.uia_diag(f"{action}.begin", target=name, window=window or "<active>",
                     control_type=control_type or "<any>",
                     foreground=foreground_window_title()[:70])

        win, nodes = backend.walk_best(window)
        if win.wrapper is None:
            return {"ctrl": None, "info": None, "score": 0.0, "candidate_names": [],
                    "window": "", "failure": self._window_missing(backend, action, window)}

        dbg.uia_diag(f"{action}.enumerated", requested=window or "<active>",
                     resolved=_clean(win.title, 60), strategy=win.strategy,
                     nodes=len(nodes))

        wanted_type = _norm(control_type) if control_type else ""
        scored: List[Tuple[float, Any, Dict[str, Any], float, float]] = []
        for node in nodes:
            info = node["info"]
            if not info["visible"]:
                continue
            if wanted_type and wanted_type not in _norm(info["type"]):
                continue
            name_score = match_score(name, info["name"]) if name else (
                1.0 if info["actionable"] else 0.0)
            if name_score <= 0.0:
                continue
            fit = action_fit(info["type"], action)
            penalty = 1.0 if info["enabled"] is not False else 0.4
            total = round(name_score * fit * penalty, 3)
            scored.append((total, node["ctrl"], info, name_score, fit))

        scored.sort(key=lambda r: r[0], reverse=True)
        dbg.uia_candidates(
            name,
            [(i[2]["name"], f"{i[2]['type']}", i[0]) for i in scored],
            shown=20, total=len(scored),
        )

        best = scored[0] if scored else None
        # A perfectly-named but unusable node (a Text label next to the real
        # control) is extremely common in Settings. Climb to the nearest
        # actionable ancestor instead of clicking the label.
        if best is not None and not best[2]["actionable"] and action != "read":
            promoted = backend.actionable_ancestor(best[1])
            if promoted is not None:
                pinfo = promoted.info()
                new_total = round(best[3] * action_fit(pinfo["type"], action), 3)
                dbg.uia_diag(f"{action}.promoted_ancestor",
                             from_control=_clean(best[2]["name"], 50),
                             from_type=best[2]["type"],
                             to_type=pinfo["type"], to_name=_clean(pinfo["name"], 50),
                             score=new_total)
                if new_total >= best[0]:
                    best = (new_total, promoted, pinfo, best[3], 1.0)

        candidate_names = [i[2]["name"] for i in scored[:12] if i[2]["name"]]
        if best is None or best[0] < self.MIN_SCORE:
            score = best[0] if best else 0.0
            dbg.uia_diag(f"{action}.rejected", target=name, best_score=score,
                         min_score=self.MIN_SCORE, candidates=len(scored))
            actionable = _actionable_names(nodes)
            return {
                "ctrl": None, "info": None, "score": score,
                "candidate_names": candidate_names, "window": win.title,
                "failure": {
                    "ok": False, "action": action, "score": score,
                    "window": win.title,
                    "reason": self._not_found_reason(name, action, win.title, score),
                    "candidates": candidate_names or actionable[:12],
                    "actionable": actionable[:25],
                },
            }

        dbg.uia_diag(f"{action}.decision", target=name,
                     chosen=_clean(best[2]["name"], 60), type=best[2]["type"],
                     path=_clean(best[2]["path"], 70),
                     score=best[0], name_score=best[3], action_fit=best[4],
                     min_score=self.MIN_SCORE, accepted=True)
        return {"ctrl": best[1], "info": best[2], "score": best[0],
                "candidate_names": candidate_names, "window": win.title,
                "nodes": nodes, "failure": None}

    @staticmethod
    def _not_found_reason(name: str, action: str, window: str, score: float) -> str:
        if score > 0:
            return (f"found something similar to '{_clean(name, 50)}' in '{_clean(window, 40)}' "
                    f"but nothing you can {action} (best match scored {score:.2f})")
        return f"no control named '{_clean(name, 50)}' in '{_clean(window, 40)}'"

    @staticmethod
    def _window_missing(backend, action: str, window: Optional[str]) -> Dict[str, Any]:
        """Honest, actionable failure when the window could not be resolved."""
        titles = []
        try:
            titles = backend.window_titles()
        except Exception:  # noqa: BLE001
            pass
        dbg.uia_diag("window.unresolved", requested=window or "<active>",
                     open_windows=" | ".join(titles[:12]))
        if window:
            reason = (f"there is no open window matching '{_clean(window, 40)}'. "
                      f"Open windows: {', '.join(titles[:10]) or 'none'}")
        else:
            reason = "could not determine the active window"
        return {"ok": False, "action": action, "reason": reason, "windows": titles[:15]}


# --------------------------------------------------------------------------- #
# selection helpers (pure)
# --------------------------------------------------------------------------- #
def _select_controls(nodes: List[Dict[str, Any]], control_type: Optional[str],
                     include_text: bool, limit: int) -> Dict[str, Any]:
    """Pick the controls worth showing the agent: actionable, visible, named."""
    wanted = _norm(control_type) if control_type else ""
    actionable: List[Dict[str, Any]] = []
    textual: List[Dict[str, Any]] = []
    for node in nodes:
        info = node["info"]
        if not info["visible"]:
            continue
        if wanted and wanted not in _norm(info["type"]):
            continue
        if not (info["name"] or "").strip():
            continue
        if info["actionable"]:
            actionable.append(info)
        elif include_text or wanted:
            textual.append(info)

    cap = max(1, int(limit))
    chosen = actionable[:cap]
    if len(chosen) < cap:
        chosen = chosen + textual[: cap - len(chosen)]
    return {
        "controls": chosen,
        "actionable_total": len(actionable),
        "truncated": (len(actionable) + len(textual)) > len(chosen),
    }


# Buttons whose LABEL states the transition, e.g. "Turn off now", "Switch on".
# Generic wording, not tied to any application.
_STATE_BUTTON_ON = re.compile(r"\b(?:turn|switch|toggle)\s+(?:it\s+)?on\b|\benable\b")
_STATE_BUTTON_OFF = re.compile(r"\b(?:turn|switch|toggle)\s+(?:it\s+)?off\b|\bdisable\b")


def _find_state_button(nodes: List[Dict[str, Any]], on: bool):
    """First visible, actionable control whose label offers this transition."""
    pattern = _STATE_BUTTON_ON if on else _STATE_BUTTON_OFF
    for node in nodes:
        info = node["info"]
        if not info["actionable"] or not info["visible"]:
            continue
        if info.get("enabled") is False:
            continue
        if pattern.search(_norm(info["name"])):
            return node["ctrl"], info
    return None


def _actionable_names(nodes: List[Dict[str, Any]]) -> List[str]:
    out = []
    for node in nodes:
        info = node["info"]
        if info["actionable"] and info["visible"] and (info["name"] or "").strip():
            out.append(f"{info['name']} ({info['type']})")
    return out


# --------------------------------------------------------------------------- #
# pywinauto backend -- ONLY ever touched from the UI apartment
# --------------------------------------------------------------------------- #
class _ResolvedWindow:
    __slots__ = ("wrapper", "title", "strategy", "alternatives")

    def __init__(self, wrapper: Any, title: str = "", strategy: str = "",
                 alternatives: Optional[List[Tuple[Any, str]]] = None) -> None:
        self.wrapper = wrapper
        self.title = title
        self.strategy = strategy
        # Same-titled runners-up, tried when the winner turns out to be empty.
        self.alternatives = alternatives or []


class _PywinautoControl:
    """Adapter around a pywinauto element. Lives and dies inside the apartment."""

    __slots__ = ("_el", "_path")

    def __init__(self, element, path: str = "") -> None:
        self._el = element
        self._path = path

    # -- reading ------------------------------------------------------- #
    def info(self) -> Dict[str, Any]:
        """Describe this control.

        Each property read is a separate COM round trip, and a browser window can
        hold thousands of nodes, so the expensive reads are done only where they
        can possibly matter: toggle state for types that have one, enabled state
        for controls the agent might act on.
        """
        name = self._safe(lambda: self._el.window_text() or "", "")
        ctype = self._control_type()
        actionable = is_actionable(ctype)
        toggle = self.toggle_state() if _norm(ctype) in TOGGLE_TYPES else None
        enabled = self._safe(lambda: bool(self._el.is_enabled()), None) if actionable else None
        return {
            "name": name,
            "type": ctype,
            "path": self._path,
            "toggle": toggle,
            "enabled": enabled,
            "visible": self._visible(),
            "actionable": actionable,
        }

    def snapshot(self) -> Dict[str, Any]:
        return {"toggle": self.toggle_state(),
                "enabled": self._safe(lambda: bool(self._el.is_enabled()), None)}

    def _control_type(self) -> str:
        return self._safe(lambda: self._el.element_info.control_type or "", "")

    def _visible(self) -> bool:
        """Visible means: reported visible AND has a non-empty rectangle.

        Off-screen and zero-size nodes are present in the tree but cannot be
        operated, and offering them to the agent produces guaranteed failures.
        """
        if self._safe(lambda: bool(self._el.is_visible()), True) is False:
            return False
        rect = self._safe(lambda: self._el.element_info.rectangle, None)
        if rect is None:
            return True
        try:
            return (rect.right - rect.left) > 0 and (rect.bottom - rect.top) > 0
        except Exception:  # noqa: BLE001
            return True

    def toggle_state(self):
        st = self._safe(lambda: self._el.get_toggle_state(), None)
        if st == 1:
            return True
        if st == 0:
            return False
        return None

    def text_value(self):
        for getter in (lambda: self._el.get_value(),
                       lambda: self._el.window_text()):
            value = self._safe(getter, None)
            if value:
                return str(value)
        return None

    # -- acting -------------------------------------------------------- #
    def invoke(self) -> str:
        """Press this control. Returns the technique that worked."""
        errors = []
        for label, fn in (
            ("invoke", lambda: self._el.invoke()),
            ("select", lambda: self._el.select()),
            ("click_input", lambda: self._click_input()),
        ):
            try:
                fn()
                return label
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{label}: {_clean(exc, 60)}")
        raise RuntimeError("; ".join(errors))

    def _click_input(self):
        # A physical click needs the control on screen and the window in front.
        self._safe(lambda: self._el.set_focus(), None)
        return self._el.click_input()

    def set_toggle(self, on: bool) -> str:
        try:
            self._el.toggle()
            return "toggle"
        except Exception as exc:  # noqa: BLE001
            dbg.uia_diag("control.toggle_unsupported",
                         control=_clean(self._safe(lambda: self._el.window_text(), ""), 50),
                         error=_clean(exc, 90))
        return self.invoke()

    def settle_toggle(self, wanted: bool, attempts: int = 6, delay: float = 0.25):
        """Poll the control's own state after acting. UI state is not instant."""
        state = None
        for _ in range(max(1, attempts)):
            state = self.toggle_state()
            if state is not None and bool(state) == bool(wanted):
                return state
            time.sleep(delay)
        return state

    def set_text(self, text: str) -> str:
        try:
            self._el.set_edit_text(text)
            return "set_edit_text"
        except Exception as exc:  # noqa: BLE001
            dbg.uia_diag("control.set_edit_text_unsupported",
                         control=_clean(self._safe(lambda: self._el.window_text(), ""), 50),
                         error=_clean(exc, 90))
        self._el.set_focus()
        try:
            self._el.type_keys("^a{BACKSPACE}", set_foreground=False)
        except Exception:  # noqa: BLE001
            pass
        self._el.type_keys(text, with_spaces=True, set_foreground=False)
        return "type_keys"

    @staticmethod
    def _safe(fn: Callable[[], Any], default: Any) -> Any:
        try:
            return fn()
        except Exception:  # noqa: BLE001
            return default


class _PywinautoBackend:
    """Wraps pywinauto's UIA Desktop. Constructed inside the UI apartment."""

    def __init__(self) -> None:
        if _LIBRARY != "pywinauto":
            raise ImportError(f"unsupported UIA_LIBRARY '{_LIBRARY}'")
        from pywinauto import Desktop  # noqa: F401 -- raises if missing
        self._Desktop = Desktop
        self._desktop = Desktop(backend="uia")

    def available(self) -> bool:
        return True

    # -- windows ------------------------------------------------------- #
    def window_titles(self) -> List[str]:
        titles: List[str] = []
        try:
            for w in self._desktop.windows():
                try:
                    title = (w.window_text() or "").strip()
                except Exception:  # noqa: BLE001
                    continue
                if title and title not in titles:
                    titles.append(title)
        except Exception:  # noqa: BLE001
            pass
        return titles

    def resolve_window(self, hint: Optional[str]) -> _ResolvedWindow:
        """Find the window the caller means.

        Order matters. The old code only tried a title regex, so a hint like
        "Display" (a *page* inside Settings, not a window title) never resolved
        and every call silently returned an empty tree.
        """
        if not hint:
            return self._active_window()

        low = _norm(hint)
        deadline = time.time() + _WINDOW_WAIT
        while True:
            candidates: List[Tuple[float, Any, str]] = []
            try:
                windows = list(self._desktop.windows())
            except Exception:  # noqa: BLE001
                windows = []
            for w in windows:
                try:
                    wr = w if hasattr(w, "descendants") else w.wrapper_object()
                    title = (wr.window_text() or "").strip()
                except Exception:  # noqa: BLE001
                    continue
                if not title:
                    continue
                tl = _norm(title)
                if tl == low:
                    score = 1.0
                elif low in tl:
                    score = 0.9
                elif tl in low:
                    score = 0.75
                else:
                    score = match_score(hint, title) * 0.7
                if score > 0:
                    # Windows can share a title: a live Settings window and an
                    # empty leftover one both report "Settings". Prefer the one
                    # that is actually on screen, so we don't drive the corpse.
                    score += 0.05 if self._on_screen(wr) else 0.0
                    candidates.append((score, wr, title))
            if candidates:
                candidates.sort(key=lambda r: r[0], reverse=True)
                score, wrapper, title = candidates[0]
                return _ResolvedWindow(
                    wrapper, title, f"title-match({score:.2f})",
                    alternatives=[(w, t) for _, w, t in candidates[1:4]],
                )
            if time.time() >= deadline:
                break
            time.sleep(0.3)

        # No window by that name. Do NOT silently substitute the active window
        # here -- acting on the wrong window is worse than reporting honestly.
        # walk_best() decides whether the hint is a *page* inside the active
        # window, which it can only tell by looking at that window's contents.
        return _ResolvedWindow(None, "", "unresolved")

    @staticmethod
    def activate(wrapper) -> bool:
        """Restore and focus a window so its automation tree is live."""
        acted = False
        try:
            if wrapper.is_minimized():
                wrapper.restore()
                acted = True
        except Exception:  # noqa: BLE001
            pass
        try:
            wrapper.set_focus()
            acted = True
        except Exception as exc:  # noqa: BLE001
            dbg.uia_diag("backend.activate_failed", error=_clean(exc, 90))
        if acted:
            time.sleep(0.4)  # let the window realise its tree
        return acted

    @staticmethod
    def _on_screen(wrapper) -> bool:
        """Visible with a real, non-zero rectangle."""
        try:
            if not wrapper.is_visible():
                return False
        except Exception:  # noqa: BLE001
            pass
        try:
            rect = wrapper.rectangle()
            return (rect.right - rect.left) > 0 and (rect.bottom - rect.top) > 0
        except Exception:  # noqa: BLE001
            return True

    def walk_best(self, hint: Optional[str]) -> Tuple[_ResolvedWindow, List[Dict[str, Any]]]:
        """Resolve a window and walk it, moving on if the winner has no content.

        A same-titled but empty window would otherwise produce "0 controls",
        which reads like a broken engine when the real window is right there.
        """
        win = self.resolve_window(hint)
        if win.wrapper is not None:
            nodes = self.walk(win.wrapper)
            if nodes:
                return win, nodes
            # A minimised or background UWP/XAML window (Settings is one) suspends
            # and exposes an EMPTY automation tree. Bringing it forward is not a
            # nicety here, it is what makes the tree exist at all.
            if self.activate(win.wrapper):
                nodes = self.walk(win.wrapper)
                dbg.uia_diag("backend.walk_after_activate",
                             window=_clean(win.title, 50), nodes=len(nodes))
                if nodes:
                    return _ResolvedWindow(win.wrapper, win.title,
                                           win.strategy + "+activated"), nodes
            for wrapper, title in win.alternatives:
                dbg.uia_diag("backend.window_empty_retry", skipped=_clean(win.title, 50),
                             trying=_clean(title, 50))
                nodes = self.walk(wrapper)
                if nodes:
                    return _ResolvedWindow(wrapper, title, win.strategy + "+alt"), nodes
            return win, []

        if not hint:
            return win, []

        # The hint matched no window title. It may name a *page* rather than a
        # window: "Display", "Bluetooth" and "Windows Update" all live inside the
        # window titled "Settings". Accept the active window only when it really
        # contains something by that name -- proof, not assumption.
        active = self._active_window()
        if active.wrapper is None:
            return win, []
        nodes = self.walk(active.wrapper)
        if nodes and self._contains_hint(nodes, hint):
            dbg.uia_diag("backend.hint_is_page", hint=_clean(hint, 40),
                         inside=_clean(active.title, 50))
            return _ResolvedWindow(active.wrapper, active.title,
                                   f"page '{_clean(hint, 24)}' inside active window"), nodes
        dbg.uia_diag("backend.hint_not_a_window", hint=_clean(hint, 40),
                     active=_clean(active.title, 50), nodes=len(nodes))
        return _ResolvedWindow(None, "", "unresolved"), []

    @staticmethod
    def _contains_hint(nodes: List[Dict[str, Any]], hint: str) -> bool:
        for node in nodes:
            if match_score(hint, node["info"]["name"]) >= 0.9:
                return True
        return False

    def _active_window(self) -> _ResolvedWindow:
        try:
            spec = self._desktop.window(active_only=True)
            spec.wait("exists", timeout=1.5)
            wr = spec.wrapper_object()
            return _ResolvedWindow(wr, (wr.window_text() or "").strip(), "active")
        except Exception:  # noqa: BLE001
            pass
        try:
            for w in self._desktop.windows():
                try:
                    wr = w.wrapper_object() if not hasattr(w, "descendants") else w
                    if wr.is_visible():
                        return _ResolvedWindow(wr, (wr.window_text() or "").strip(),
                                               "first-visible")
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass
        return _ResolvedWindow(None, "", "unresolved")

    # -- tree ---------------------------------------------------------- #
    def walk(self, window_wrapper) -> List[Dict[str, Any]]:
        """Breadth-first walk yielding {ctrl, info} with a readable path.

        Bounded by _MAX_NODES: a browser content tree can be tens of thousands
        of nodes, and no prompt can use them anyway.
        """
        out: List[Dict[str, Any]] = []
        roots: List[Any] = []
        try:
            roots = list(window_wrapper.children() or [])
        except Exception as exc:  # noqa: BLE001
            dbg.uia_diag("backend.children_failed", error=_clean(exc, 100))

        # Some windows -- notably UWP/XAML ones like Settings -- report no direct
        # children while still exposing a full descendant tree. Walking children
        # only would silently return an empty snapshot, which is exactly what made
        # "Settings has no controls" look like a timeout. Fall back to the flat
        # descendant list (no hierarchy paths, but every control is present).
        if not roots:
            try:
                flat = list(window_wrapper.descendants() or [])
            except Exception as exc:  # noqa: BLE001
                dbg.uia_diag("backend.descendants_failed", error=_clean(exc, 100))
                return out
            dbg.uia_diag("backend.walk_flat", reason="window reports no children",
                         descendants=len(flat))
            for element in flat[:_MAX_NODES]:
                ctrl = _PywinautoControl(element)
                try:
                    out.append({"ctrl": ctrl, "info": ctrl.info()})
                except Exception:  # noqa: BLE001
                    continue
            return out

        frontier: List[Tuple[Any, str, int]] = [(child, "", 0) for child in roots]
        while frontier and len(out) < _MAX_NODES:
            element, parent_path, depth = frontier.pop(0)
            ctrl = _PywinautoControl(element, parent_path)
            try:
                info = ctrl.info()
            except Exception:  # noqa: BLE001
                continue
            out.append({"ctrl": ctrl, "info": info})

            if depth >= 12:
                continue
            label = (info["name"] or info["type"] or "").strip()[:28]
            path = f"{parent_path} > {label}" if parent_path and label else (label or parent_path)
            try:
                children = element.children()
            except Exception:  # noqa: BLE001
                continue
            for child in children:
                frontier.append((child, path, depth + 1))
        return out

    def actionable_ancestor(self, ctrl: _PywinautoControl, max_up: int = 4):
        """Climb from a matched label to the control that actually does something."""
        element = getattr(ctrl, "_el", None)
        for _ in range(max_up):
            try:
                element = element.parent()
            except Exception:  # noqa: BLE001
                return None
            if element is None:
                return None
            candidate = _PywinautoControl(element)
            try:
                if candidate.info()["actionable"]:
                    return candidate
            except Exception:  # noqa: BLE001
                return None
        return None

    # -- scrolling ----------------------------------------------------- #
    def scroll(self, window_wrapper, direction: str, amount: int) -> str:
        direction = _norm(direction) or "down"
        amount = max(1, min(int(amount or 3), 15))
        try:
            window_wrapper.set_focus()
        except Exception:  # noqa: BLE001
            pass
        try:
            wheel = -amount if direction in ("down", "niche", "neeche") else amount
            window_wrapper.wheel_mouse_input(coords=None, wheel_dist=wheel)
            return "wheel"
        except Exception:  # noqa: BLE001
            pass
        keys = {"down": "{PGDN}", "up": "{PGUP}",
                "top": "{HOME}", "bottom": "{END}"}.get(direction, "{PGDN}")
        try:
            window_wrapper.type_keys(keys * amount, set_foreground=False)
            return "keys"
        except Exception:  # noqa: BLE001
            return ""


# --------------------------------------------------------------------------- #
# singleton
# --------------------------------------------------------------------------- #
_engine: Optional[UIAEngine] = None
_engine_lock = threading.Lock()


def get_uia_engine() -> UIAEngine:
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = UIAEngine()
    return _engine
