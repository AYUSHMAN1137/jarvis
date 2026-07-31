"""Checker (Phase 4, Chunk 2 + 3) -- verifies that an action actually worked.

Strategy (locked): watcher-first, vision-fallback (hybrid).
  1. Read the live system state from the watcher and compare it against what
     the action was supposed to do (open/close/toggle).
  2. If the watcher can't decide (UNKNOWN) AND the action is one we know how to
     judge visually, fall back to the Gemini vision verifier.
  3. Every verdict carries a `reason` + `evidence` (19 Jun refinement) so we can
     report honestly and debug.

The Checker is ALWAYS background (subscribes to `action.done`) -- never inline
on the voice path, so it never slows a response. `verify()` itself is a pure
function of (tool, args, state), which makes it fully unit-testable without a
live OS.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional

import config as _cfg
from app.services.agent.checker import models
from app.services.agent.checker.models import VerifyResult
from app.services.debug_logger import dbg

logger = logging.getLogger("J.A.R.V.I.S")

_SETTLE_SECONDS = float(getattr(_cfg, "CHECKER_SETTLE_SECONDS", 1.5))
_VISION_ENABLED = bool(getattr(_cfg, "CHECKER_VISION_ENABLED", True))
# Families where a screenshot can genuinely settle an UNKNOWN verdict.
_VISION_FAMILIES = frozenset({"open", "close", "toggle", "ui", "file"})

# Keys we look at (in order) to figure out what the action targeted.
_TARGET_KEYS = (
    "name", "app", "application", "app_name", "window", "title",
    "url", "website", "site", "query", "text", "path", "file", "folder",
)


def _norm(s: Any) -> str:
    return str(s or "").lower().replace(".exe", "").strip()


def _target(args: Dict[str, Any]) -> str:
    if not isinstance(args, dict):
        return ""
    for k in _TARGET_KEYS:
        v = args.get(k)
        if v:
            return str(v).strip()
    return ""


def _match(target: str, candidates: List[str]) -> Optional[str]:
    """Return the first candidate that shares a meaningful token with target."""
    t = _norm(target)
    if not t:
        return None
    toks = [w for w in re.split(r"[\s\-_/.]+", t) if len(w) >= 3] or [t]
    for cand in candidates:
        nc = _norm(cand)
        if not nc:
            continue
        for w in toks:
            if w in nc:
                return cand
    return None


# Families a tool may declare via @tool(verification={"family": ...}).
#
#   open/close/toggle/ui/file/input  -- observable state change (watcher/disk/UIA)
#   query                            -- read-only, no side effect to verify
#   frontend                         -- browser-bound, verified by the dispatch ack
#   memory                           -- writes to memory.db, verified by re-reading
#   google                           -- writes via a Google API, verified by re-query
#   none                             -- honestly unverifiable (e.g. shutdown)
KNOWN_FAMILIES = frozenset({
    "open", "close", "toggle", "ui", "file", "input",
    "query", "frontend", "memory", "google", "none",
})


def declared_family(tool: str) -> Optional[str]:
    """Family declared on the tool itself, if any.

    Reading it from @tool metadata is what keeps this generic: adding a tool
    never means editing a list in here (project rule: no hardcoded command
    lists -- integrate through tool metadata).
    """
    try:
        from app.services.agent.tool_registry import registry
        spec = registry.get(tool)
        if spec is None:
            return None
        family = str((getattr(spec, "verification", {}) or {}).get("family", "")).strip().lower()
        return family if family in KNOWN_FAMILIES else None
    except Exception:  # noqa: BLE001 - classification must never raise
        return None


def classify_family(tool: str) -> Optional[str]:
    """Map a tool to a verifiable family, or None if we can't judge it.

    Declared metadata wins; the name heuristics below are only a fallback for
    tools that have not been tagged yet.

    Every family here widens what the system can learn: promotion requires a
    PASS, and a family with no verifier can only ever produce UNKNOWN. Measured
    before this change, 43 of 75 registered tools fell through to None -- so
    they could never be verified, cached, or turned into a habit, and the cache
    held 6 entries with 2 lifetime hits.

    Name-order matters in the fallback. UI tools are checked first because names
    like `ui_click` also contain "click"/"open"-ish words, and because they are
    verified by reading the control back rather than by inspecting watcher state.
    """
    declared = declared_family(tool)
    if declared:
        return declared

    t = (tool or "").lower()
    if t.startswith("ui_") or t in ("check_for_updates",):
        return "ui"
    if any(k in t for k in ("save", "write_file", "create_file", "download")):
        return "file"
    if any(k in t for k in ("hotkey", "type_text", "clipboard", "focus_window",
                            "window_action", "scroll")):
        return "input"
    if any(k in t for k in ("close", "kill", "quit", "terminate")):
        return "close"
    if any(k in t for k in ("open", "launch", "start_app")):
        return "open"
    if any(k in t for k in ("wifi", "bluetooth", "airplane", "volume", "brightness", "mute")):
        return "toggle"
    return None


class Checker:
    def __init__(self, bus=None, vision=None, watcher_getter=None,
                 settle_seconds: Optional[float] = None) -> None:
        self.bus = bus
        self.vision = vision
        self._watcher_getter = watcher_getter
        self.settle_seconds = _SETTLE_SECONDS if settle_seconds is None else float(settle_seconds)
        # After the settle sleep we re-read FRESH state and poll briefly: the
        # watcher caches settings/windows on a throttle, so a single immediate
        # read can be STALE and produce a false FAIL (Wi-Fi/Bluetooth/brightness
        # still showing the old value, or a just-closed window still listed).
        self._poll_window = float(getattr(_cfg, "CHECKER_VERIFY_POLL_SECONDS", 4.0))
        self._poll_interval = float(getattr(_cfg, "CHECKER_VERIFY_POLL_INTERVAL", 0.4))
        if bus is not None:
            bus.subscribe("action.done", self._on_action_done)

    # -- live state ------------------------------------------------------ #
    def _state(self) -> dict:
        try:
            if self._watcher_getter is not None:
                return self._watcher_getter() or {}
            from app.services.watcher import get_watcher
            return get_watcher().get_state() or {}
        except Exception as e:  # noqa: BLE001
            logger.debug("[CHECKER] state read failed: %s", e)
            return {}

    def _fresh_state(self) -> dict:
        """Like _state() but forces the watcher to refresh first, defeating the
        throttled-snapshot staleness that caused false FAILs on toggles/close."""
        try:
            if self._watcher_getter is not None:
                return self._watcher_getter() or {}
            from app.services.watcher import get_watcher
            w = get_watcher()
            refresh = getattr(w, "refresh_now", None)
            if callable(refresh):
                try:
                    fresh = refresh()
                    if isinstance(fresh, dict) and fresh:
                        return fresh
                except Exception:  # noqa: BLE001
                    pass
            return w.get_state() or {}
        except Exception as e:  # noqa: BLE001
            logger.debug("[CHECKER] fresh state read failed: %s", e)
            return {}

    # -- pure verification (unit-testable) ------------------------------- #
    def verify(self, tool: str, args: Dict[str, Any], state: Dict[str, Any]) -> VerifyResult:
        """Watcher-only verification. Pure function of inputs; never raises."""
        try:
            family = classify_family(tool)
            if family == "open":
                return self._verify_open(args, state)
            if family == "close":
                return self._verify_close(args, state)
            if family == "toggle":
                return self._verify_toggle(tool, args, state)
            if family == "ui":
                return self._verify_ui(tool, args, state)
            if family == "file":
                return self._verify_file(tool, args, state)
            if family == "input":
                return self._verify_input(tool, args, state)
            if family == "query":
                return self._verify_query(tool, args, state)
            if family == "memory":
                return self._verify_memory(tool, args, state)
            if family == "google":
                return self._verify_google(tool, args, state)
            if family == "frontend":
                # Settled in _on_action_done, where the action_id needed to look
                # up the browser acknowledgement is available.
                return VerifyResult(models.UNKNOWN, reason="awaiting browser acknowledgement",
                                    source="frontend")
            if family == "none":
                return VerifyResult(models.UNKNOWN,
                                    reason="this action cannot be verified by design",
                                    source="none")
            return VerifyResult(models.UNKNOWN, reason="no verifier for this action", source="watcher")
        except Exception as e:  # noqa: BLE001
            return VerifyResult(models.UNKNOWN, reason=f"verify error: {e}", source="watcher")

    def _candidates(self, state: Dict[str, Any]) -> List[str]:
        cands: List[str] = []
        for app in (state.get("launched") or []):
            if isinstance(app, dict) and app.get("name"):
                cands.append(str(app["name"]))
        for w in (state.get("windows") or []):
            cands.append(str(w))
        return cands

    def _verify_open(self, args, state) -> VerifyResult:
        target = _target(args)
        if not target:
            return VerifyResult(models.UNKNOWN, reason="no target in args", source="watcher")
        matched = _match(target, self._candidates(state))
        if matched:
            return VerifyResult(
                models.PASS, reason=f"'{target}' is open",
                evidence=f"window/app: {matched}", source="watcher",
            )
        # Not seen yet -> let vision decide (watcher may lag, or it's a browser tab).
        return VerifyResult(
            models.UNKNOWN, reason=f"'{target}' not seen in watcher state",
            source="watcher",
        )

    def _verify_close(self, args, state) -> VerifyResult:
        target = _target(args)
        if not target:
            return VerifyResult(models.UNKNOWN, reason="no target in args", source="watcher")
        matched = _match(target, self._candidates(state))
        if matched:
            return VerifyResult(
                models.FAIL, reason=f"'{target}' is still open",
                evidence=f"still present: {matched}", source="watcher",
            )
        return VerifyResult(
            models.PASS, reason=f"'{target}' is closed",
            evidence="no matching window/app", source="watcher",
        )

    def _verify_toggle(self, tool, args, state) -> VerifyResult:
        t = (tool or "").lower()
        settings = state.get("settings") or {}
        action = _norm(args.get("action") or args.get("state") or args.get("mode"))
        want_on = action in ("on", "enable", "true", "1")
        want_off = action in ("off", "disable", "false", "0")

        def cmp_radio(key: str, label: str) -> VerifyResult:
            cur = _norm(settings.get(key))
            if not cur or cur == "unknown":
                return VerifyResult(models.UNKNOWN, reason=f"{label} state unknown", source="watcher")
            if want_on or want_off:
                expected = "on" if want_on else "off"
                if cur == expected:
                    return VerifyResult(models.PASS, reason=f"{label} is {cur}",
                                        evidence=f"{key}={cur}", source="watcher")
                return VerifyResult(models.FAIL, reason=f"{label} is {cur}, expected {expected}",
                                    evidence=f"{key}={cur}", source="watcher")
            return VerifyResult(models.UNKNOWN, reason=f"no clear target for {label}", source="watcher")

        if "wifi" in t:
            return cmp_radio("wifi", "Wi-Fi")
        if "bluetooth" in t:
            return cmp_radio("bluetooth", "Bluetooth")
        if "airplane" in t:
            # airplane on => radios off
            wifi = _norm(settings.get("wifi"))
            if not wifi or wifi == "unknown":
                return VerifyResult(models.UNKNOWN, reason="airplane state unknown", source="watcher")
            if want_on:
                ok = wifi == "off"
                return VerifyResult(models.PASS if ok else models.FAIL,
                                    reason=f"airplane on => Wi-Fi {wifi}",
                                    evidence=f"wifi={wifi}", source="watcher")
            if want_off:
                ok = wifi == "on"
                return VerifyResult(models.PASS if ok else models.FAIL,
                                    reason=f"airplane off => Wi-Fi {wifi}",
                                    evidence=f"wifi={wifi}", source="watcher")
        # NB: check mute BEFORE volume -- the mute tool is named 'mute_volume',
        # which also contains 'volume'; without this guard it would wrongly take
        # the level-compare path and never reach the mute logic below.
        if ("volume" in t or "brightness" in t) and "mute" not in t:
            key = "volume" if "volume" in t else "brightness"
            cur = settings.get(key)
            want = args.get("level", args.get("value"))
            if cur is None or want is None:
                return VerifyResult(models.UNKNOWN, reason=f"{key} not readable", source="watcher")
            try:
                ok = abs(float(cur) - float(want)) <= 5
                return VerifyResult(models.PASS if ok else models.FAIL,
                                    reason=f"{key} is {cur} (target {want})",
                                    evidence=f"{key}={cur}", source="watcher")
            except (TypeError, ValueError):
                return VerifyResult(models.UNKNOWN, reason=f"{key} not numeric", source="watcher")
        if "mute" in t:
            muted = settings.get("muted")
            if muted is None:
                return VerifyResult(models.UNKNOWN, reason="mute state unknown", source="watcher")
            cur_muted = bool(muted)
            # Work out the DESIRED state -- a PASS must mean we reached it, not
            # merely that we could read SOME mute value (the old always-PASS bug
            # that reported 'unmute' as done even when audio was still muted).
            want_muted: Optional[bool] = None
            if isinstance(args.get("mute"), bool):
                want_muted = args.get("mute")
            elif "unmute" in t or action in ("unmute", "off", "false", "0"):
                want_muted = False
            elif action in ("mute", "on", "true", "1"):
                want_muted = True
            if want_muted is None:
                return VerifyResult(models.UNKNOWN, reason="no clear mute target",
                                    evidence=f"muted={cur_muted}", source="watcher")
            if cur_muted == want_muted:
                return VerifyResult(models.PASS,
                                    reason=f"audio is {'muted' if cur_muted else 'unmuted'}",
                                    evidence=f"muted={cur_muted}", source="watcher")
            return VerifyResult(models.FAIL,
                                reason=(f"audio is {'muted' if cur_muted else 'unmuted'}, "
                                        f"expected {'muted' if want_muted else 'unmuted'}"),
                                evidence=f"muted={cur_muted}", source="watcher")
        return VerifyResult(models.UNKNOWN, reason="unhandled toggle", source="watcher")

    # -- UI verifier: read the control back ------------------------------ #
    def _verify_ui(self, tool: str, args: Dict[str, Any], state: Dict[str, Any]) -> VerifyResult:
        """Verify a UI action by re-reading the control it acted on.

        The screen is its own source of truth. For a toggle we compare the
        control's live state against what was asked for -- the only honest proof
        a switch flipped. For clicks and typing there is no persistent state to
        compare, so we defer to the vision fallback rather than inventing a PASS.
        """
        target = str(args.get("target") or args.get("name") or "").strip()
        window = str(args.get("window") or "").strip() or None
        action = str(args.get("action") or "").strip().lower()
        wants_toggle = ("toggle" in (tool or "").lower()) or action == "toggle"

        if not target:
            return VerifyResult(models.UNKNOWN, reason="no control name in args",
                                source="ui")
        if not wants_toggle:
            # A click has no readable after-state. Say so plainly; the vision
            # fallback is allowed to judge this one.
            return VerifyResult(models.UNKNOWN,
                                reason="clicks leave no readable state", source="ui")

        wanted = args.get("state")
        if wanted is None:
            wanted = args.get("on")
        if not isinstance(wanted, bool):
            return VerifyResult(models.UNKNOWN, reason="no desired state in args",
                                source="ui")

        try:
            from app.services.agent.automation import get_uia_engine
            read = get_uia_engine().read_state(target, window=window)
        except Exception as exc:  # noqa: BLE001
            return VerifyResult(models.UNKNOWN, reason=f"could not read control: {exc}",
                                source="ui")

        if not read.get("ok"):
            return VerifyResult(models.UNKNOWN,
                                reason=f"control '{target}' not readable: {read.get('reason', '')}"[:160],
                                source="ui")
        current = read.get("toggle")
        if current is None:
            # No toggle property. Fall back to reading the transition button:
            # a window offering "Turn off now" is currently on.
            try:
                from app.services.agent.automation import get_uia_engine
                inferred = get_uia_engine().read_switch_state(window=window)
            except Exception as exc:  # noqa: BLE001
                inferred = {"ok": False, "reason": str(exc)}
            if inferred.get("ok"):
                current = bool(inferred.get("state"))
                if current == bool(wanted):
                    return VerifyResult(
                        models.PASS,
                        reason=f"'{target}' is {'on' if current else 'off'}",
                        evidence=str(inferred.get("evidence", "")), source="ui")
                return VerifyResult(
                    models.FAIL,
                    reason=(f"'{target}' is {'on' if current else 'off'}, "
                            f"expected {'on' if wanted else 'off'}"),
                    evidence=str(inferred.get("evidence", "")), source="ui")
            return VerifyResult(models.UNKNOWN,
                                reason=f"'{target}' reports no on/off state", source="ui")
        if bool(current) == bool(wanted):
            return VerifyResult(models.PASS,
                                reason=f"'{target}' is {'on' if current else 'off'}",
                                evidence=f"{read.get('name')}={'on' if current else 'off'}",
                                source="ui")
        return VerifyResult(models.FAIL,
                            reason=(f"'{target}' is {'on' if current else 'off'}, "
                                    f"expected {'on' if wanted else 'off'}"),
                            evidence=f"{read.get('name')}={'on' if current else 'off'}",
                            source="ui")

    # -- file verifier: does the file exist now? -------------------------- #
    def _verify_file(self, tool: str, args: Dict[str, Any], state: Dict[str, Any]) -> VerifyResult:
        """A save/write is provable: the file is either on disk or it isn't."""
        import os

        raw = ""
        for key in ("path", "file", "filename", "file_path", "filepath", "destination"):
            value = args.get(key)
            if value:
                raw = str(value).strip()
                break
        if not raw:
            return VerifyResult(models.UNKNOWN, reason="no file path in args", source="file")
        candidate = os.path.expandvars(os.path.expanduser(raw))
        try:
            if os.path.isfile(candidate):
                size = os.path.getsize(candidate)
                return VerifyResult(models.PASS, reason=f"'{os.path.basename(candidate)}' exists",
                                    evidence=f"{candidate} ({size} bytes)", source="file")
            if os.path.isdir(candidate):
                return VerifyResult(models.PASS, reason="folder exists",
                                    evidence=candidate, source="file")
        except OSError as exc:
            return VerifyResult(models.UNKNOWN, reason=f"path not checkable: {exc}",
                                source="file")
        return VerifyResult(models.FAIL, reason=f"'{raw}' was not created",
                            evidence=f"missing: {candidate}", source="file")

    # -- input verifier: did focus land where we typed? ------------------ #
    def _verify_input(self, tool: str, args: Dict[str, Any], state: Dict[str, Any]) -> VerifyResult:
        """Keystrokes and clipboard writes are weak evidence by nature.

        We can at least prove the obvious failure mode: typing into whatever
        window happened to be in front. Reporting that honestly is far better
        than the old blanket "Typed the text." with no verification at all.
        """
        active = str(state.get("active_window") or state.get("window") or "").strip()
        wanted_window = str(args.get("window") or args.get("title") or "").strip()

        if "clipboard" in (tool or "").lower():
            text = str(args.get("text") or "")
            if not text:
                return VerifyResult(models.UNKNOWN, reason="nothing to compare", source="clipboard")
            try:
                import pyperclip
                current = pyperclip.paste() or ""
            except Exception as exc:  # noqa: BLE001
                return VerifyResult(models.UNKNOWN, reason=f"clipboard unreadable: {exc}",
                                    source="clipboard")
            if _norm(text)[:200] == _norm(current)[:200]:
                return VerifyResult(models.PASS, reason="clipboard holds the text",
                                    evidence=current[:80], source="clipboard")
            return VerifyResult(models.FAIL, reason="clipboard does not hold that text",
                                evidence=current[:80], source="clipboard")

        if wanted_window and active:
            if _match(wanted_window, [active]):
                return VerifyResult(models.PASS, reason=f"'{active}' has focus",
                                    evidence=f"active: {active}", source="watcher")
            return VerifyResult(models.FAIL,
                                reason=f"'{active}' has focus, not '{wanted_window}'",
                                evidence=f"active: {active}", source="watcher")
        if active:
            return VerifyResult(models.UNKNOWN,
                                reason=f"input went to '{active}'; no target given to compare",
                                evidence=f"active: {active}", source="watcher")
        return VerifyResult(models.UNKNOWN, reason="active window unknown", source="watcher")

    # -- query verifier: read-only tools have nothing to verify ---------- #
    def _verify_query(self, tool: str, args: Dict[str, Any],
                      state: Dict[str, Any]) -> VerifyResult:
        """A read-only tool changes nothing, so there is no state to check.

        Reaching this point means the tool returned without an ERROR (a tool
        error is turned into a definitive FAIL earlier, in `_on_action_done`),
        so the honest verdict is PASS on transport evidence. The reason string
        says exactly that -- it does not claim a state change was confirmed,
        because there was none to confirm.
        """
        return VerifyResult(models.PASS,
                            reason="read-only tool returned data; no state change to verify",
                            evidence=f"{tool} has no side effect", source="transport")

    # -- memory verifier: did the fact really land in the DB? ------------ #
    def _verify_memory(self, tool: str, args: Dict[str, Any],
                       state: Dict[str, Any]) -> VerifyResult:
        """Memory writes are cheap to prove: read the store back."""
        try:
            from app.services.memory_service import get_memory
            memory = get_memory()
        except Exception as exc:  # noqa: BLE001
            return VerifyResult(models.UNKNOWN, reason=f"memory unavailable: {exc}",
                                source="memory")
        if not getattr(memory, "enabled", False):
            return VerifyResult(models.UNKNOWN, reason="memory is disabled", source="memory")

        needle = str(args.get("text") or args.get("value") or args.get("fact")
                     or args.get("query") or "").strip()
        if not needle:
            return VerifyResult(models.UNKNOWN, reason="nothing to look up", source="memory")

        try:
            found = memory.recall(needle, limit=4) or ""
        except Exception as exc:  # noqa: BLE001
            return VerifyResult(models.UNKNOWN, reason=f"recall failed: {exc}",
                                source="memory")

        t = (tool or "").lower()
        if "forget" in t:
            if found:
                return VerifyResult(models.FAIL, reason="the memory is still there",
                                    evidence=found[:120], source="memory")
            return VerifyResult(models.PASS, reason="the memory is gone", source="memory")
        if found:
            return VerifyResult(models.PASS, reason="stored and readable back",
                                evidence=found[:120], source="memory")
        return VerifyResult(models.FAIL, reason="not found in memory after writing",
                            evidence=needle[:80], source="memory")

    # -- google verifier: re-query the API for what we just changed ------ #
    def _verify_google(self, tool: str, args: Dict[str, Any],
                       state: Dict[str, Any]) -> VerifyResult:
        """Calendar/Drive writes are provable by asking Google again.

        Deliberately conservative: without a searchable title we return UNKNOWN
        rather than assume success, because a silent API failure is exactly the
        case this is meant to catch.
        """
        if "gmail" in (tool or "").lower():
            return self._verify_gmail(args)

        title = str(args.get("title") or args.get("summary") or args.get("name")
                    or args.get("query") or "").strip()
        if not title:
            return VerifyResult(models.UNKNOWN, reason="no title to search for",
                                source="google")
        try:
            from app.services.agent import deps
            calendar = getattr(deps, "calendar_service", None)
            lookup = getattr(calendar, "search_events_summary", None)
            if lookup is None:
                return VerifyResult(models.UNKNOWN, reason="calendar lookup unavailable",
                                    source="google")
            summary = str(lookup(title) or "")
        except Exception as exc:  # noqa: BLE001
            return VerifyResult(models.UNKNOWN, reason=f"re-query failed: {exc}",
                                source="google")

        # search_events_summary returns formatted text, so presence of the title
        # in the result is the signal. A "no events" style answer will not
        # contain it.
        present = bool(title) and title.lower() in summary.lower()

        if "delete" in (tool or "").lower():
            if present:
                return VerifyResult(models.FAIL, reason=f"'{title}' is still on the calendar",
                                    evidence=summary[:120], source="google")
            return VerifyResult(models.PASS, reason=f"'{title}' is gone", source="google")
        if present:
            return VerifyResult(models.PASS, reason=f"'{title}' is on the calendar",
                                evidence=summary[:120], source="google")
        return VerifyResult(models.FAIL, reason=f"'{title}' was not created",
                            evidence=summary[:120], source="google")

    def _verify_gmail(self, args: Dict[str, Any]) -> VerifyResult:
        """A sent mail is provable: it appears in Sent.

        This is the one action in the whole tool set that cannot be undone, so
        claiming success without checking would be the worst possible failure.
        """
        subject = str(args.get("subject") or "").strip()
        if not subject:
            return VerifyResult(models.UNKNOWN, reason="no subject to search for",
                                source="google")
        try:
            from app.services.agent import deps
            gmail = getattr(deps, "gmail_service", None)
            lookup = getattr(gmail, "find_sent_message", None)
            if lookup is None:
                return VerifyResult(models.UNKNOWN, reason="Sent lookup unavailable",
                                    source="google")
            found = bool(lookup(subject))
        except Exception as exc:  # noqa: BLE001
            return VerifyResult(models.UNKNOWN, reason=f"Sent check failed: {exc}",
                                source="google")
        if found:
            return VerifyResult(models.PASS, reason=f"'{subject}' is in Sent",
                                source="google")
        return VerifyResult(models.FAIL, reason=f"'{subject}' is not in Sent",
                            source="google")

    # -- frontend verifier: did the browser accept the dispatch? --------- #
    def _verify_frontend(self, tool: str, args: Dict[str, Any],
                         action_id: str) -> VerifyResult:
        """Browser-bound tools produce no server-side effect.

        The only evidence available is the acknowledgement the browser posts
        back to /api/activity/frontend-ack. That proves the browser accepted the
        action -- not that a page finished loading -- and the verdict says so.
        """
        target = _target(args) or str(args.get("url") or "")
        try:
            from app.services.agent.checker import get_phase4
            result = get_phase4().dispatch_result(action_id)
        except Exception as exc:  # noqa: BLE001
            return VerifyResult(models.UNKNOWN, reason=f"ack lookup failed: {exc}",
                                source="frontend")

        if result is None:
            return VerifyResult(models.UNKNOWN,
                                reason="browser never acknowledged the action",
                                evidence=target[:80], source="frontend")
        if result.get("accepted"):
            return VerifyResult(models.PASS, reason="browser accepted the action",
                                evidence=target[:80] or result.get("tool", ""),
                                source="frontend")
        return VerifyResult(models.FAIL,
                            reason=result.get("error") or "browser rejected the action",
                            evidence=target[:80], source="frontend")

    # -- live verify (used by background subscribers / learner) ---------- #
    def verify_live(self, tool: str, args: Dict[str, Any]) -> VerifyResult:
        return self.verify(tool, args, self._state())

    def _vision_question(self, tool: str, args: Dict[str, Any]) -> str:
        family = classify_family(tool)
        target = _target(args)
        if family == "open":
            return f"Is the application or page '{target}' now open and visible on screen?"
        if family == "close":
            return f"Has the application or window '{target}' been closed (it should NOT be visible)?"
        if family == "toggle":
            return f"Did the setting change requested by '{tool}' take effect on screen?"
        if family == "ui":
            control = str(args.get("target") or args.get("name") or "").strip()
            action = str(args.get("action") or "").strip().lower()
            if action == "type" or "type_into" in (tool or ""):
                text = str(args.get("text") or "")[:40]
                return f"Does a text field on screen now contain '{text}'?"
            return (f"Was the control '{control}' activated? Look for the change it "
                    "should have caused on screen, not just for the control itself.")
        if family == "file":
            return f"Did a save or download of '{target}' complete on screen?"
        if family == "input":
            return f"Did the keyboard input for '{tool}' reach the intended window?"
        return f"Did the action '{tool}' succeed?"

    def _verify_settling(self, tool: str, args: Dict[str, Any]) -> VerifyResult:
        """Verify after a short FRESH-state poll.

        A single read right after an action can be stale (throttled watcher
        snapshot) and yield a false FAIL. We sleep the settle, then re-read
        fresh state and poll until the first PASS; otherwise we keep the last
        definitive (FAIL) verdict, and only fall back to UNKNOWN if nothing was
        ever decided -- so honesty is preserved either way.
        """
        initial_delay = self.settle_seconds
        poll_interval = max(self._poll_interval, 0.05)
        poll_window = max(self._poll_window, 0.0)
        try:
            from app.services.agent.tool_registry import registry
            spec = registry.get(tool)
            profile = str((getattr(spec, "verification", {}) or {}).get("settle_profile", ""))
            profiles = getattr(_cfg, "CHECKER_SETTLE_PROFILES", {})
            if profile in profiles:
                initial_delay, poll_interval, poll_window = profiles[profile]
        except Exception:
            pass
        if initial_delay > 0:
            time.sleep(initial_delay)
        res = self.verify(tool, args, self._fresh_state())
        if res.verdict == models.PASS:
            return res
        deadline = time.time() + poll_window
        while time.time() < deadline:
            time.sleep(poll_interval)
            nxt = self.verify(tool, args, self._fresh_state())
            if nxt.verdict == models.PASS:
                return nxt
            if nxt.verdict != models.UNKNOWN:
                res = nxt  # prefer a definitive FAIL over a stale UNKNOWN
        return res

    # -- background entrypoint ------------------------------------------- #
    def _on_action_done(self, payload: dict) -> None:
        try:
            tool = payload.get("tool", "")
            args = payload.get("args", {}) or {}
            ok = payload.get("ok", True)
            observation = str(payload.get("observation", ""))
            logger.info("[CHECKER] verifying '%s' (ok=%s)...", tool, ok)

            if not ok:
                # The tool itself reported an error -- that's a definitive FAIL.
                res = VerifyResult(
                    models.FAIL,
                    reason="tool returned an error",
                    evidence=observation[:160],
                    source="tool",
                )
            elif classify_family(tool) == "frontend":
                # Browser-bound: the only evidence is the ack the page posts
                # back, so give it a moment to arrive rather than reading an
                # empty table immediately.
                action_id = str(payload.get("action_id", ""))
                res = self._verify_frontend(tool, args, action_id)
                deadline = time.time() + max(self._poll_window, 0.0)
                while res.verdict == models.UNKNOWN and time.time() < deadline:
                    time.sleep(max(self._poll_interval, 0.05))
                    res = self._verify_frontend(tool, args, action_id)
            else:
                res = self._verify_settling(tool, args)
                # Vision fallback only where looking at the screen can actually
                # settle the question. "input" is excluded on purpose: a
                # screenshot cannot tell whether a keystroke was delivered, and
                # firing a vision call per hotkey would be pure cost.
                if (
                    res.verdict == models.UNKNOWN
                    and _VISION_ENABLED
                    and self.vision is not None
                    and classify_family(tool) in _VISION_FAMILIES
                ):
                    try:
                        if self.vision.available():
                            res = self.vision.verify(self._vision_question(tool, args))
                    except Exception as e:  # noqa: BLE001
                        logger.debug("[CHECKER] vision fallback failed: %s", e)

            logger.info(
                "[CHECKER] '%s' -> %s | %s%s | via %s",
                tool, res.verdict, res.reason,
                (" [evidence: %s]" % res.evidence) if res.evidence else "",
                res.source,
            )
            dbg.verification_event(
                tool=tool, verdict=res.verdict, reason=res.reason,
                source=res.source,
                execution_id=payload.get("execution_id", ""),
                action_id=payload.get("action_id", ""),
            )
            try:
                from app.services.memory_service import get_memory
                get_memory().update_action_verification(
                    payload.get("action_id", ""), res.verdict, res.source
                )
            except Exception:
                pass
            if self.bus is not None:
                self.bus.publish("verified", {
                    "tool": tool,
                    "args": args,
                    "ok": ok,
                    "verdict": res.verdict,
                    "reason": res.reason,
                    "evidence": res.evidence,
                    "source": res.source,
                    "user_message": payload.get("user_message", ""),
                    "execution_id": payload.get("execution_id", ""),
                    "action_id": payload.get("action_id", ""),
                    "steps": [{"tool": tool, "args": args}],
                })
        except Exception as e:  # noqa: BLE001 - subscriber must never raise
            logger.debug("[CHECKER] _on_action_done failed: %s", e)
