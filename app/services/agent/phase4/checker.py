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
from app.services.agent.phase4 import models
from app.services.agent.phase4.models import VerifyResult

logger = logging.getLogger("J.A.R.V.I.S")

_SETTLE_SECONDS = float(getattr(_cfg, "CHECKER_SETTLE_SECONDS", 1.5))
_VISION_ENABLED = bool(getattr(_cfg, "CHECKER_VISION_ENABLED", True))

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


def classify_family(tool: str) -> Optional[str]:
    """Map a tool name to a verifiable family, or None if we can't judge it."""
    t = (tool or "").lower()
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
        return f"Did the action '{tool}' succeed?"

    def _verify_settling(self, tool: str, args: Dict[str, Any]) -> VerifyResult:
        """Verify after a short FRESH-state poll.

        A single read right after an action can be stale (throttled watcher
        snapshot) and yield a false FAIL. We sleep the settle, then re-read
        fresh state and poll until the first PASS; otherwise we keep the last
        definitive (FAIL) verdict, and only fall back to UNKNOWN if nothing was
        ever decided -- so honesty is preserved either way.
        """
        if self.settle_seconds > 0:
            time.sleep(self.settle_seconds)
        res = self.verify(tool, args, self._fresh_state())
        if res.verdict == models.PASS:
            return res
        deadline = time.time() + max(self._poll_window, 0.0)
        while time.time() < deadline:
            time.sleep(max(self._poll_interval, 0.05))
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
            else:
                res = self._verify_settling(tool, args)
                # Vision fallback only when we know the family but watcher said UNKNOWN.
                if (
                    res.verdict == models.UNKNOWN
                    and _VISION_ENABLED
                    and self.vision is not None
                    and classify_family(tool) is not None
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
                    "steps": [{"tool": tool, "args": args}],
                })
        except Exception as e:  # noqa: BLE001 - subscriber must never raise
            logger.debug("[CHECKER] _on_action_done failed: %s", e)
