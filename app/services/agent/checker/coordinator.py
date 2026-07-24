"""Phase 4 coordinator -- wires the Event Bus + Checker + Vision + Skill-maker +
Learner together and exposes a tiny surface for the rest of the app.

Lifecycle:
  * get_phase4().start()  -- called once at startup (after tools are loaded).
  * publish_action_done() -- called by the agent loop after each tool (non-
    blocking, fail-soft) so the Checker can verify in the background.
  * state_note()          -- a short, optional line the agent can surface on the
    next turn ("last action verified / could not be verified").
  * stop()                -- called at shutdown.

EVERYTHING here is fail-soft: if Phase 4 is disabled or anything fails to wire,
JARVIS behaves exactly like Phase 3.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Dict, Optional

import config as _cfg
from app.services.agent.checker import models
from app.services.agent.checker.event_bus import get_event_bus

logger = logging.getLogger("J.A.R.V.I.S")

_ENABLED = bool(getattr(_cfg, "PHASE4_ENABLED", True))
_VISION_ENABLED = bool(getattr(_cfg, "CHECKER_VISION_ENABLED", True))


class Phase4Coordinator:
    def __init__(self) -> None:
        self.enabled = _ENABLED
        self.started = False
        self.bus = None
        self.store = None
        self.checker = None
        self.vision = None
        self.skill_maker = None
        self.learner = None
        self._last_verified: Optional[Dict[str, Any]] = None
        # Rolling activity feed for the /dashboard (independent of state_note()).
        self._recent: deque = deque(maxlen=60)
        self._started_at = time.time()

    # -- lifecycle ------------------------------------------------------- #
    def start(self) -> None:
        if not self.enabled:
            logger.info("[PHASE4] disabled (PHASE4_ENABLED=False) -- running as Phase 3.")
            return
        if self.started:
            return
        try:
            from app.services.agent.checker.skill_store import SkillStore
            from app.services.agent.checker.skill_maker import SkillMaker
            from app.services.agent.checker.checker import Checker
            from app.services.agent.checker.learner import Learner

            self.bus = get_event_bus()
            self.store = SkillStore()

            if _VISION_ENABLED:
                try:
                    from app.services.agent.checker.vision_verifier import VisionVerifier
                    self.vision = VisionVerifier()
                except Exception as e:  # noqa: BLE001
                    logger.warning("[PHASE4] vision verifier unavailable: %s", e)
                    self.vision = None

            self.checker = Checker(bus=self.bus, vision=self.vision)
            self.skill_maker = SkillMaker(self.store, self.bus)

            registry = None
            try:
                from app.services.agent.tool_registry import registry as _registry
                registry = _registry
            except Exception as e:  # noqa: BLE001
                logger.debug("[PHASE4] tool registry unavailable for learner: %s", e)
            self.learner = Learner(
                bus=self.bus, registry=registry, verify_fn=self.checker.verify_live
            )

            # Keep the latest verdict so the agent can mention it next turn,
            # and feed a rolling activity log that the dashboard reads.
            self.bus.subscribe("verified", self._remember_verified)
            self.bus.subscribe(
                "learner.needs_confirmation",
                lambda p: self._remember_learner("NEEDS_CONFIRM", p),
            )
            self.bus.subscribe(
                "learner.stuck", lambda p: self._remember_learner("STUCK", p)
            )

            self.started = True
            logger.info(
                "[PHASE4] online -- Checker(%s vision) + Skill-maker(N=%d) + Learner(maxRetry=%d).",
                "with" if self.vision is not None else "no",
                getattr(self.store, "min_repeats", 3),
                getattr(self.learner, "max_retries", 2),
            )
        except Exception as e:  # noqa: BLE001 - never block startup
            logger.warning("[PHASE4] failed to start (non-fatal): %s", e)
            self.started = False

    def stop(self) -> None:
        try:
            if self.bus is not None:
                self.bus.shutdown()
        except Exception as e:  # noqa: BLE001
            logger.debug("[PHASE4] stop error: %s", e)

    # -- agent loop hook ------------------------------------------------- #
    def publish_action_done(self, tool: str, args: dict, observation: Any,
                            user_message: str = "", execution_id: str = "",
                            action_id: str = "") -> None:
        """Fire-and-forget from the agent loop. Never blocks, never raises."""
        if not self.started or self.bus is None:
            return
        try:
            obs = str(observation)
            self.bus.publish("action.done", {
                "tool": tool,
                "args": args or {},
                "ok": not obs.startswith("ERROR"),
                "observation": obs[:500],
                "user_message": user_message or "",
                "execution_id": execution_id or "",
                "action_id": action_id or "",
            })
        except Exception as e:  # noqa: BLE001
            logger.debug("[PHASE4] publish_action_done failed: %s", e)

    def publish_execution_completed(self, execution_id: str, user_message: str,
                                    steps: list, ok: bool) -> None:
        """Publish the complete resolved execution for execution-level learning.

        Individual action verdicts may arrive before or after this event. Phase 6
        joins them by execution/action IDs and promotes only the complete run.
        """
        if not self.started or self.bus is None or not execution_id:
            return
        try:
            self.bus.publish("execution.completed", {
                "execution_id": execution_id,
                "user_message": user_message or "",
                "steps": list(steps or []),
                "ok": bool(ok),
            })
        except Exception as e:  # noqa: BLE001
            logger.debug("[PHASE4] publish_execution_completed failed: %s", e)

    # -- surfacing ------------------------------------------------------- #
    def _remember_verified(self, payload: dict) -> None:
        try:
            self._last_verified = dict(payload or {})
            self._recent.append({
                "time": time.time(),
                "kind": "verified",
                "tool": payload.get("tool", ""),
                "verdict": payload.get("verdict", ""),
                "reason": payload.get("reason", ""),
                "evidence": payload.get("evidence", ""),
                "source": payload.get("source", ""),
            })
        except Exception:  # noqa: BLE001
            pass

    def _remember_learner(self, kind: str, payload: dict) -> None:
        try:
            self._recent.append({
                "time": time.time(),
                "kind": "learner",
                "tool": (payload or {}).get("tool", ""),
                "verdict": kind,
                "reason": (payload or {}).get("reason", ""),
                "evidence": "",
                "source": "learner",
            })
        except Exception:  # noqa: BLE001
            pass

    def recent_activity(self, limit: int = 30) -> list:
        """Newest-first activity feed (verifications + learner events)."""
        try:
            items = list(self._recent)[-int(limit):]
            return list(reversed(items))
        except Exception:  # noqa: BLE001
            return []

    def health(self) -> dict:
        """Compact subsystem health for the dashboard."""
        h = {
            "enabled": self.enabled,
            "started": self.started,
            "uptime_seconds": int(max(0, time.time() - self._started_at)),
            "vision": self.vision is not None,
            "bus": self.bus is not None,
            "checker": self.checker is not None,
            "learner": self.learner is not None,
            "skills": bool(getattr(self.store, "enabled", False)) if self.store else False,
        }
        return h

    def state_note(self) -> str:
        """Optional one-liner for the agent's state block (verification + any
        honest learner notes). Returns '' when there's nothing to say."""
        if not self.started:
            return ""
        parts = []
        try:
            lv = self._last_verified
            if lv and lv.get("verdict") in (models.PASS, models.FAIL):
                tool = lv.get("tool", "action")
                parts.append(
                    f"[Verification] last action '{tool}': {lv.get('verdict')}"
                    + (f" ({lv.get('reason')})" if lv.get("reason") else "")
                )
            if self.learner is not None:
                notes = self.learner.pop_notes()
                for n in notes:
                    parts.append("[Learner] " + n)
        except Exception:  # noqa: BLE001
            return ""
        return "\n".join(parts[:4])

    def stats(self) -> dict:
        out = {"enabled": self.enabled, "started": self.started}
        try:
            if self.bus is not None:
                out["bus"] = self.bus.stats()
            if self.store is not None:
                out["skills"] = self.store.stats()
        except Exception:  # noqa: BLE001
            pass
        return out


# --------------------------------------------------------------------------- #
# singleton
# --------------------------------------------------------------------------- #
_coord: Optional[Phase4Coordinator] = None
_coord_lock = threading.Lock()


def get_phase4() -> Phase4Coordinator:
    global _coord
    if _coord is None:
        with _coord_lock:
            if _coord is None:
                _coord = Phase4Coordinator()
    return _coord
