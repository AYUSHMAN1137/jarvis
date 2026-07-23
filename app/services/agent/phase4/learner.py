"""Learner (Phase 4, Chunk 5).

Reacts to verified FAILs and decides what to do -- risk-tiered, with an honest
stop (locked decision):

  * SAFE + idempotent action (toggles, focus, open/open-url): silently retry the
    SAME action up to LEARNER_MAX_RETRIES (2-3). After each retry it re-verifies;
    on success it stops happily, on exhaustion it records an HONEST stop note
    (never a fake "done", never an infinite loop).
  * RISKY / irreversible action (delete, send, shutdown, kill, sign-out, ...):
    NEVER auto-retried. It records a "needs confirmation" note for the user.
  * Optional research hook (web how-to) behind a safe-gate, off by default.

Only idempotent tools are auto-retried so a false-FAIL can't cause a damaging
or duplicate side effect (e.g. opening a second window). Re-applying a toggle
that already happened is a harmless no-op -- that's why toggles are the safe
set.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any, Callable, Deque, Dict, Optional

import config as _cfg
from app.services.agent.phase4 import models

logger = logging.getLogger("J.A.R.V.I.S")

_MAX_RETRIES = int(getattr(_cfg, "LEARNER_MAX_RETRIES", 2))
_RESEARCH_ENABLED = bool(getattr(_cfg, "LEARNER_RESEARCH_ENABLED", False))

# Clearly irreversible / side-effectful -- never auto-retry, always confirm.
_IRREVERSIBLE = {
    "delete_file", "delete", "empty_recycle_bin", "hibernate_computer",
    "sign_out", "shutdown", "restart", "kill_process", "send_email",
    "send", "format", "move_file", "rename_file",
}
# Idempotent + reversible -- re-running is harmless, so safe to silently retry.
_IDEMPOTENT_SAFE = {
    "open_application", "open_app", "launch_app", "open_website", "open_url",
    "wifi_control", "bluetooth_control", "set_volume", "set_brightness",
    "airplane_mode", "focus_window",
}


class Learner:
    def __init__(self, bus=None, registry=None,
                 verify_fn: Optional[Callable[[str, dict], Any]] = None,
                 max_retries: Optional[int] = None,
                 research_enabled: Optional[bool] = None) -> None:
        self.bus = bus
        self.registry = registry
        self.verify_fn = verify_fn
        self.max_retries = _MAX_RETRIES if max_retries is None else int(max_retries)
        self.research_enabled = _RESEARCH_ENABLED if research_enabled is None else bool(research_enabled)
        self._retries: Dict[str, int] = {}
        self._notes: Deque[str] = deque(maxlen=20)
        if bus is not None:
            bus.subscribe("verified", self._on_verified)

    # -- risk classification -------------------------------------------- #
    def risk_tier(self, tool: str) -> str:
        """Return 'risky' or 'safe' for a tool name."""
        t = (tool or "").lower()
        if t in _IRREVERSIBLE:
            return "risky"
        try:
            if self.registry is not None and self.registry.is_dangerous(tool):
                return "risky"
        except Exception:  # noqa: BLE001
            pass
        return "safe"

    def _is_idempotent(self, tool: str) -> bool:
        return (tool or "").lower() in _IDEMPOTENT_SAFE

    # -- notes (surfaced to the user on the next turn) ------------------- #
    def recent_notes(self) -> list:
        return list(self._notes)

    def pop_notes(self) -> list:
        notes = list(self._notes)
        self._notes.clear()
        return notes

    def _note(self, msg: str) -> None:
        self._notes.append(msg)
        logger.info("[LEARNER] %s", msg)

    # -- main reaction --------------------------------------------------- #
    def _on_verified(self, payload: dict) -> None:
        try:
            if (payload or {}).get("verdict") != models.FAIL:
                return
            tool = payload.get("tool", "")
            args = payload.get("args", {}) or {}
            label = (payload.get("user_message") or tool or "action").strip()
            reason = payload.get("reason", "")
            sig = tool + "|" + str(sorted(args.items()) if isinstance(args, dict) else args)

            tier = self.risk_tier(tool)
            if tier == "risky":
                self._note(
                    f"'{label}' verify FAIL ({reason}) -- risky action, maine khud retry "
                    f"nahi kiya. Aap confirm karein to dobara karu."
                )
                self._publish("learner.needs_confirmation", tool, args, reason)
                return

            # SAFE tier
            if not self._is_idempotent(tool):
                # Safe but not safe-to-repeat -> honest stop, no blind retry.
                self._note(
                    f"'{label}' verify FAIL ({reason}). Yeh safe-but-not-repeatable hai, "
                    f"maine retry nahi kiya -- aap bata dein."
                )
                self._research(label)
                return

            count = self._retries.get(sig, 0)
            if count >= self.max_retries:
                self._note(
                    f"'{label}' {self.max_retries} retries ke baad bhi verify FAIL ({reason}). "
                    f"Main ruk raha hoon -- jhooth nahi bolunga ki ho gaya."
                )
                self._publish("learner.stuck", tool, args, reason)
                self._research(label)
                return

            # Silent safe retry of the SAME idempotent action.
            self._retries[sig] = count + 1
            logger.info("[LEARNER] safe retry %d/%d: %s", count + 1, self.max_retries, tool)
            self._retry(tool, args)
            # Re-verify the retry, if we can.
            if self.verify_fn is not None:
                try:
                    res = self.verify_fn(tool, args)
                    if getattr(res, "verdict", None) == models.PASS:
                        self._retries.pop(sig, None)
                        logger.info("[LEARNER] recovered '%s' on retry.", label)
                        return
                except Exception as e:  # noqa: BLE001
                    logger.debug("[LEARNER] re-verify failed: %s", e)
            if count + 1 >= self.max_retries:
                self._note(
                    f"'{label}' retry ke baad bhi verify nahi hua ({reason}). Ruk raha hoon."
                )
                self._publish("learner.stuck", tool, args, reason)
        except Exception as e:  # noqa: BLE001 - subscriber must never raise
            logger.debug("[LEARNER] _on_verified failed: %s", e)

    def _retry(self, tool: str, args: dict) -> str:
        if self.registry is None:
            return ""
        try:
            return str(self.registry.execute(tool, args))
        except Exception as e:  # noqa: BLE001
            logger.debug("[LEARNER] retry execute failed: %s", e)
            return ""

    def _publish(self, event: str, tool: str, args: dict, reason: str) -> None:
        if self.bus is None:
            return
        try:
            self.bus.publish(event, {"tool": tool, "args": args, "reason": reason})
        except Exception:  # noqa: BLE001
            pass

    def _research(self, query: str) -> None:
        """Optional how-to lookup when stuck (safe-gated, off by default)."""
        if not self.research_enabled or not query:
            return
        try:
            from app.services.agent.tools import web_tools  # noqa: F401
            logger.info("[LEARNER] (research enabled) would look up how-to for: %s", query[:80])
            # Intentionally light: research is advisory only and must never act.
        except Exception as e:  # noqa: BLE001
            logger.debug("[LEARNER] research hook failed: %s", e)
