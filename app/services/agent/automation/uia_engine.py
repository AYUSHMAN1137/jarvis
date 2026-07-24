"""UIA engine (Phase 5, Chunk 1) -- find and drive real Windows UI controls.

Why this exists
---------------
Phase 1-4 could open apps and toggle radios, but it was BLIND to the actual UI:
it could not click a button, flip a toggle switch, or pick a menu item that only
exists on screen. That is exactly why "play any song" only opened a search page
and never pressed play, and why some Settings toggles never actually flipped.

The UIA engine closes that gap GENERICALLY (no per-app hardcode): it walks the
Windows UI Automation tree (via pywinauto) and finds a control by its NAME and
(optionally) its TYPE, then invokes / toggles / types into it. The SAME code
clicks a YouTube play button, a Settings toggle, or a Notepad menu item.

Design rules (locked)
---------------------
* Reliability #1 -- every call is wrapped and fail-soft. A missing package, a
  control that isn't found, or a backend error returns a clear, honest result
  ({ok: False, reason: ...}) and NEVER raises, NEVER fakes success.
* No hardcode -- controls are matched by name/type tokens, not by fixed
  coordinates or app-specific magic.
* Lazy + optional -- pywinauto is imported the first time the engine is used,
  so the server still boots on a machine without it (UIA just reports
  unavailable).
* Testable without Windows -- the engine talks to a small BACKEND interface, so
  unit tests inject a mock UI tree and exercise all of the matching / clicking /
  toggling logic with no real OS.

Backend contract (duck-typed)
-----------------------------
A backend exposes:
    available() -> bool
    find(name, window, control_type, timeout) -> list[control]
Each returned control exposes:
    .name           -> str
    .control_type   -> str   (e.g. "Button", "CheckBox", "Edit", "MenuItem")
    .toggle_state   -> bool | None   (None if it isn't a toggle)
    .invoke()       -> None  (click / press)
    .toggle(on)     -> None  (set a toggle/checkbox to on/off)
    .set_text(text) -> None  (type into an edit control)
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any, Dict, List, Optional

import config as _cfg

logger = logging.getLogger("J.A.R.V.I.S")

_ENABLED = bool(getattr(_cfg, "UIA_ENABLED", True))
_LIBRARY = str(getattr(_cfg, "UIA_LIBRARY", "pywinauto")).lower()
_FIND_TIMEOUT = float(getattr(_cfg, "UIA_FIND_TIMEOUT", 5.0))


def _clean(s: Any, limit: int = 80) -> str:
    """Collapse whitespace + clip -- used for safe, leak-free log lines."""
    return " ".join(str(s or "").split())[:limit]


def _norm(s: Any) -> str:
    return str(s or "").strip().lower()


def _tokens(s: str) -> List[str]:
    return [w for w in re.split(r"[\s\-_/.,:]+", _norm(s)) if len(w) >= 2]


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


class UIAEngine:
    """Generic Windows UI Automation driver (find -> click/toggle/type)."""

    # Minimum name-match score to accept a control as "the one".
    MIN_SCORE = 0.5

    def __init__(self, backend: Any = None, find_timeout: Optional[float] = None,
                 enabled: Optional[bool] = None) -> None:
        self._backend = backend
        self._timeout = float(find_timeout if find_timeout is not None else _FIND_TIMEOUT)
        self._enabled = _ENABLED if enabled is None else bool(enabled)
        self._backend_tried = False

    # -- backend management --------------------------------------------- #
    def _get_backend(self):
        if self._backend is not None:
            return self._backend
        if self._backend_tried:
            return None
        self._backend_tried = True
        try:
            self._backend = _PywinautoBackend()
            logger.info("[UIA] backend ready: %s", _LIBRARY)
        except Exception as e:  # noqa: BLE001
            logger.warning("[UIA] backend unavailable (%s): %s", _LIBRARY, _clean(e))
            self._backend = None
        return self._backend

    def available(self) -> bool:
        """True only if the engine is enabled AND a backend really loaded."""
        if not self._enabled:
            return False
        b = self._get_backend()
        try:
            return bool(b is not None and b.available())
        except Exception:  # noqa: BLE001
            return False

    # -- internal: find the best-matching control ----------------------- #
    def _find(self, name: str, window: Optional[str], control_type: Optional[str]):
        """Return (control, score, candidates) -- never raises."""
        b = self._get_backend()
        if b is None:
            return None, 0.0, []
        try:
            controls = list(
                b.find(name=name, window=window, control_type=control_type,
                       timeout=self._timeout) or []
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[UIA] find raised: %s", _clean(e))
            return None, 0.0, []
        best, best_score = None, 0.0
        cand_names = []
        for c in controls:
            cname = getattr(c, "name", "") or ""
            cand_names.append(cname)
            if control_type and _norm(getattr(c, "control_type", "")) != _norm(control_type):
                # When a type filter is given, only score same-type controls.
                if _norm(control_type) not in _norm(getattr(c, "control_type", "")):
                    continue
            score = match_score(name, cname) if name else 0.6
            if score > best_score:
                best, best_score = c, score
        return best, best_score, cand_names

    # -- public actions (all return a structured, honest dict) ---------- #
    def _unavailable(self, action: str) -> Dict[str, Any]:
        return {
            "ok": False, "action": action, "available": False,
            "reason": (
                "UIA engine unavailable -- the 'pywinauto' package isn't "
                "installed (pip install pywinauto) or this isn't Windows."
            ),
        }

    def list_controls(self, window: Optional[str] = None,
                      control_type: Optional[str] = None,
                      limit: int = 40) -> Dict[str, Any]:
        if not self.available():
            return self._unavailable("list")
        b = self._get_backend()
        # Use a thread timeout so heavy windows (Chrome) don't hang forever
        result_holder: list = []
        error_holder: list = []

        def _do_find():
            try:
                result_holder.append(
                    b.find(name=None, window=window, control_type=control_type,
                           timeout=self._timeout) or []
                )
            except Exception as e:  # noqa: BLE001
                error_holder.append(e)

        t = threading.Thread(target=_do_find, daemon=True)
        t.start()
        t.join(timeout=10.0)  # max 10s for enumeration

        if t.is_alive():
            logger.warning("[UIA] list_controls TIMED OUT (10s) window=%s", _clean(window))
            return {"ok": False, "action": "list",
                    "reason": f"list_controls timed out (10s) -- window '{_clean(window)}' has too many controls. Try a more specific control_type or use ui_click directly."}
        if error_holder:
            return {"ok": False, "action": "list", "reason": f"list failed: {_clean(error_holder[0])}"}

        controls = list(result_holder[0]) if result_holder else []
        items = []
        for c in controls[: max(1, int(limit))]:
            items.append({
                "name": getattr(c, "name", "") or "",
                "type": getattr(c, "control_type", "") or "",
                "toggle": getattr(c, "toggle_state", None),
            })
        logger.info("[UIA] list_controls window=%s -> %d control(s)",
                    _clean(window) or "<active>", len(items))
        return {"ok": True, "action": "list", "count": len(items), "controls": items}

    def find(self, name: str, window: Optional[str] = None,
             control_type: Optional[str] = None) -> Dict[str, Any]:
        if not self.available():
            return self._unavailable("find")
        ctrl, score, cands = self._find(name, window, control_type)
        if ctrl is None or score < self.MIN_SCORE:
            logger.info("[UIA] find '%s' -> no confident match (best=%.2f among %d)",
                        _clean(name), score, len(cands))
            return {
                "ok": False, "action": "find", "reason": f"no control matching '{_clean(name)}'",
                "candidates": cands[:12], "score": score,
            }
        logger.info("[UIA] find '%s' -> '%s' (%s, score=%.2f)", _clean(name),
                    _clean(getattr(ctrl, "name", "")), getattr(ctrl, "control_type", ""), score)
        return {
            "ok": True, "action": "find", "score": score,
            "name": getattr(ctrl, "name", ""), "type": getattr(ctrl, "control_type", ""),
            "toggle": getattr(ctrl, "toggle_state", None),
        }

    def click(self, name: str, window: Optional[str] = None,
              control_type: Optional[str] = None) -> Dict[str, Any]:
        if not self.available():
            return self._unavailable("click")
        ctrl, score, cands = self._find(name, window, control_type)
        if ctrl is None or score < self.MIN_SCORE:
            logger.info("[UIA] click '%s' -> not found (best=%.2f)", _clean(name), score)
            return {"ok": False, "action": "click",
                    "reason": f"control '{_clean(name)}' not found",
                    "candidates": cands[:12]}
        try:
            ctrl.invoke()
        except Exception as e:  # noqa: BLE001
            logger.warning("[UIA] click '%s' failed: %s", _clean(name), _clean(e))
            return {"ok": False, "action": "click", "reason": f"click failed: {_clean(e)}"}
        evidence = f"clicked '{_clean(getattr(ctrl, 'name', name))}'"
        logger.info("[UIA] click -> %s (score=%.2f)", evidence, score)
        return {"ok": True, "action": "click", "evidence": evidence,
                "name": getattr(ctrl, "name", ""), "score": score}

    def set_toggle(self, name: str, on: bool, window: Optional[str] = None,
                   control_type: Optional[str] = None) -> Dict[str, Any]:
        if not self.available():
            return self._unavailable("toggle")
        ctrl, score, cands = self._find(name, window, control_type)
        if ctrl is None or score < self.MIN_SCORE:
            logger.info("[UIA] toggle '%s' -> not found (best=%.2f)", _clean(name), score)
            return {"ok": False, "action": "toggle",
                    "reason": f"toggle '{_clean(name)}' not found",
                    "candidates": cands[:12]}
        cur = getattr(ctrl, "toggle_state", None)
        if cur is not None and bool(cur) == bool(on):
            logger.info("[UIA] toggle '%s' already %s -- no-op", _clean(name), "on" if on else "off")
            return {"ok": True, "action": "toggle", "changed": False,
                    "evidence": f"'{_clean(getattr(ctrl, 'name', name))}' already {'on' if on else 'off'}",
                    "state": bool(on)}
        try:
            if hasattr(ctrl, "toggle"):
                ctrl.toggle(bool(on))
            else:
                ctrl.invoke()
        except Exception as e:  # noqa: BLE001
            logger.warning("[UIA] toggle '%s' failed: %s", _clean(name), _clean(e))
            return {"ok": False, "action": "toggle", "reason": f"toggle failed: {_clean(e)}"}
        evidence = f"set '{_clean(getattr(ctrl, 'name', name))}' -> {'on' if on else 'off'}"
        logger.info("[UIA] %s (score=%.2f)", evidence, score)
        return {"ok": True, "action": "toggle", "changed": True, "evidence": evidence,
                "state": bool(on)}

    def set_text(self, name: str, text: str, window: Optional[str] = None,
                 control_type: Optional[str] = None) -> Dict[str, Any]:
        if not self.available():
            return self._unavailable("set_text")
        ctrl, score, cands = self._find(name, window, control_type or "Edit")
        if ctrl is None or score < self.MIN_SCORE:
            logger.info("[UIA] set_text '%s' -> field not found (best=%.2f)", _clean(name), score)
            return {"ok": False, "action": "set_text",
                    "reason": f"text field '{_clean(name)}' not found",
                    "candidates": cands[:12]}
        try:
            ctrl.set_text(str(text))
        except Exception as e:  # noqa: BLE001
            logger.warning("[UIA] set_text '%s' failed: %s", _clean(name), _clean(e))
            return {"ok": False, "action": "set_text", "reason": f"set_text failed: {_clean(e)}"}
        logger.info("[UIA] set_text '%s' -> '%s' (score=%.2f)", _clean(name), _clean(text), score)
        return {"ok": True, "action": "set_text",
                "evidence": f"typed into '{_clean(getattr(ctrl, 'name', name))}'"}


# --------------------------------------------------------------------------- #
# Real backend: pywinauto (UI Automation). Imported lazily; wrapped so a
# missing package or a backend hiccup degrades to "unavailable" instead of
# crashing the server.
# --------------------------------------------------------------------------- #
class _PywinautoControl:
    """Adapter around a pywinauto wrapper so the engine stays backend-agnostic."""

    def __init__(self, element) -> None:
        self._el = element

    @property
    def name(self) -> str:
        try:
            return self._el.window_text() or ""
        except Exception:  # noqa: BLE001
            return ""

    @property
    def control_type(self) -> str:
        try:
            return self._el.element_info.control_type or ""
        except Exception:  # noqa: BLE001
            return ""

    @property
    def toggle_state(self):
        try:
            st = self._el.get_toggle_state()  # 0 off, 1 on, 2 indeterminate
            if st == 1:
                return True
            if st == 0:
                return False
        except Exception:  # noqa: BLE001
            pass
        return None

    def invoke(self) -> None:
        try:
            self._el.invoke()
            return
        except Exception:  # noqa: BLE001
            pass
        # Not every control supports Invoke -- fall back to a real click.
        self._el.click_input()

    def toggle(self, on: bool) -> None:
        cur = self.toggle_state
        if cur is not None and bool(cur) == bool(on):
            return
        try:
            self._el.toggle()
            return
        except Exception:  # noqa: BLE001
            pass
        self.invoke()

    def set_text(self, text: str) -> None:
        try:
            self._el.set_edit_text(text)
            return
        except Exception:  # noqa: BLE001
            pass
        self._el.set_focus()
        self._el.type_keys(text, with_spaces=True)


class _PywinautoBackend:
    """Lazily wraps pywinauto's UIA Desktop. Raises on import so the engine can
    record 'unavailable' cleanly."""

    def __init__(self) -> None:
        if _LIBRARY != "pywinauto":
            raise ImportError(f"unsupported UIA_LIBRARY '{_LIBRARY}'")
        from pywinauto import Desktop  # noqa: F401 -- raises if missing
        self._Desktop = Desktop
        self._desktop = Desktop(backend="uia")

    def available(self) -> bool:
        return True

    def _resolve_window(self, window: Optional[str]):
        """Return a resolved pywinauto window *wrapper* (not a WindowSpecification).

        WindowSpecification is a lazy search object -- calling .descendants()
        on it fails in pywinauto 0.6.x.  We must call .wrapper_object() first
        to get the real HwndWrapper / UIAWrapper that supports .descendants().
        """
        if window:
            # Try exact-ish title_re match first.
            try:
                spec = self._desktop.window(title_re=f"(?i).*{re.escape(window)}.*")
                spec.wait("exists", timeout=4)
                return spec.wrapper_object()
            except Exception:  # noqa: BLE001
                pass
            # Fallback: iterate all top-level windows and match by substring.
            try:
                low = window.lower()
                for w in self._desktop.windows():
                    try:
                        wr = w.wrapper_object()
                        title = (wr.window_text() or "").lower()
                        if low in title:
                            return wr
                    except Exception:  # noqa: BLE001
                        continue
            except Exception:  # noqa: BLE001
                pass
            return None  # window not found

        # No window hint -> use the foreground / active window.
        try:
            spec = self._desktop.window(active_only=True)
            spec.wait("exists", timeout=2)
            return spec.wrapper_object()
        except Exception:  # noqa: BLE001
            pass
        # Last resort: first visible top-level window.
        try:
            for w in self._desktop.windows():
                try:
                    wr = w.wrapper_object()
                    if wr.is_visible():
                        return wr
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass
        return None

    def find(self, name=None, window=None, control_type=None, timeout=5.0):
        win = self._resolve_window(window)
        if win is None:
            # Window not found -- return empty so the engine reports honestly.
            return []
        try:
            descendants = win.descendants()
        except Exception:  # noqa: BLE001
            # If descendants() still fails, try children as a fallback.
            try:
                descendants = win.children()
            except Exception:  # noqa: BLE001
                return []
        out = []
        for el in descendants:
            try:
                ct = el.element_info.control_type or ""
            except Exception:  # noqa: BLE001
                ct = ""
            if control_type and _norm(control_type) not in _norm(ct):
                continue
            out.append(_PywinautoControl(el))
        return out


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
