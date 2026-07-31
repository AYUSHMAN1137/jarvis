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
from collections import OrderedDict, deque
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
        # Section 7: verification metrics
        self._verification_metrics = {
            "total_pass": 0, "total_fail": 0, "total_unknown": 0,
            "by_source": {},   # source -> count
            "timeout_count": 0,
            "missing_verifier_count": 0,
            "disagreement_count": 0,  # tool ok but verifier FAIL
        }
        # Section 7: frontend action acknowledgements
        self._pending_dispatches: Dict[str, Dict[str, Any]] = {}
        # action_id -> ack outcome. Bounded: browser actions are short-lived and
        # this only has to survive long enough for the Checker to read it.
        self._dispatch_results: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._dispatch_lock = threading.Lock()
        # M13 §3.4: verdicts keyed by action_id + an Event per waiter, so a turn
        # can wait (briefly, boundedly) for the truth before it speaks.
        self._verdicts: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._verdict_events: Dict[str, threading.Event] = {}
        self._verdict_lock = threading.Lock()

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
    @staticmethod
    def _failure_message(payload: dict) -> str:
        """Plain-English line the UI can show the user after a FAIL.

        Verification finishes *after* the reply has been streamed, so JARVIS has
        already said "done" by the time a FAIL lands. Without this the user is
        never told -- it isn't lying on purpose, it just ran out of turn. The
        checker's `reason` strings are already written in plain English
        ("'Spotify' is still open"), so they carry the correction directly.
        """
        reason = str(payload.get("reason") or "").strip()
        tool = str(payload.get("tool") or "").strip()
        if not reason:
            return f"That didn't work — {tool} reported no effect." if tool else ""
        message = f"Actually, that didn't work — {reason}."
        evidence = str(payload.get("evidence") or "").strip()
        if evidence and evidence.lower() not in reason.lower():
            message += f" ({evidence})"
        return message

    def _publish_verdict_locally(self, payload: dict) -> None:
        """Record a verdict by action_id and wake anyone waiting on it."""
        action_id = str((payload or {}).get("action_id") or "")
        if not action_id:
            return
        with self._verdict_lock:
            self._verdicts[action_id] = dict(payload or {})
            while len(self._verdicts) > 300:
                self._verdicts.popitem(last=False)
            event = self._verdict_events.get(action_id)
        if event is not None:
            event.set()

    def wait_for_verdict(self, action_id: str,
                         timeout: float = 3.0) -> Optional[Dict[str, Any]]:
        """Block up to ``timeout`` seconds for one action's verdict.

        Returns the verdict payload, or None on timeout (treated as UNKNOWN by
        the caller -- never as success). Verification runs on the event bus in a
        background thread, so without this the verdict always arrived after the
        reply had already streamed and the only possible correction was a late
        bubble in the UI.
        """
        key = str(action_id or "")
        if not key:
            return None
        with self._verdict_lock:
            existing = self._verdicts.get(key)
            if existing is not None:
                return dict(existing)
            event = self._verdict_events.get(key)
            if event is None:
                event = threading.Event()
                self._verdict_events[key] = event
        try:
            event.wait(max(0.0, float(timeout)))
        except Exception:  # noqa: BLE001
            pass
        with self._verdict_lock:
            self._verdict_events.pop(key, None)
            found = self._verdicts.get(key)
            return dict(found) if found is not None else None

    def _remember_verified(self, payload: dict) -> None:
        self._publish_verdict_locally(payload)
        try:
            self._last_verified = dict(payload or {})
            verdict = payload.get("verdict", "")
            row = {
                "time": time.time(),
                "kind": "verified",
                "tool": payload.get("tool", ""),
                "verdict": verdict,
                "reason": payload.get("reason", ""),
                "evidence": payload.get("evidence", ""),
                "source": payload.get("source", ""),
            }
            if verdict == models.FAIL:
                row["message"] = self._failure_message(payload)
            self._recent.append(row)
            # Section 7: update verification metrics
            source = payload.get("source", "none")
            if verdict == models.PASS:
                self._verification_metrics["total_pass"] += 1
            elif verdict == models.FAIL:
                self._verification_metrics["total_fail"] += 1
                # Disagreement: tool said ok but verifier said FAIL
                if payload.get("ok", True):
                    self._verification_metrics["disagreement_count"] += 1
            else:
                self._verification_metrics["total_unknown"] += 1
                reason = payload.get("reason", "")
                if "no verifier" in reason.lower():
                    self._verification_metrics["missing_verifier_count"] += 1
                if "timeout" in reason.lower():
                    self._verification_metrics["timeout_count"] += 1
            src_counts = self._verification_metrics.setdefault("by_source", {})
            src_counts[source] = src_counts.get(source, 0) + 1
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

    def verification_metrics(self) -> dict:
        """Section 7: verification metrics for the dashboard."""
        return dict(self._verification_metrics)

    # -- Section 7: frontend dispatch acknowledgement ------------------- #
    def register_dispatch(self, dispatch_id: str, action_id: str,
                         tool: str = "", execution_id: str = "") -> None:
        """Record that a frontend action was dispatched; awaiting ack.

        Called from ExecutionCoordinator.execute_action, strictly before the
        payload is handed to the chat layer, so an ack can never arrive for an
        unknown dispatch.
        """
        if not dispatch_id:
            return
        with self._dispatch_lock:
            self._pending_dispatches[dispatch_id] = {
                "dispatch_id": dispatch_id, "action_id": action_id,
                "tool": tool, "execution_id": execution_id,
                "dispatched_at": time.time(), "acknowledged": False,
            }
            self._sweep_pending_locked()

    def _sweep_pending_locked(self) -> None:
        """Drop dispatches nobody will ever acknowledge (browser closed, tab
        killed). Caller must hold `_dispatch_lock`.

        Without this the dict only ever grew: an unacknowledged dispatch has no
        other removal path, and the browser is free to simply never answer.
        """
        max_age = float(getattr(_cfg, "FRONTEND_DISPATCH_MAX_AGE", 120.0))
        cutoff = time.time() - max(5.0, max_age)
        stale = [key for key, value in self._pending_dispatches.items()
                 if float(value.get("dispatched_at", 0.0)) < cutoff]
        for key in stale:
            self._pending_dispatches.pop(key, None)

    def acknowledge_dispatch(self, dispatch_id: str, attempted: bool = True,
                             accepted: bool = True, error: str = "") -> bool:
        """Process a frontend acknowledgement. Returns True if matched."""
        with self._dispatch_lock:
            pending = self._pending_dispatches.pop(dispatch_id, None)
            if pending is not None:
                # Keep the outcome keyed by action_id so the Checker can verify
                # browser-bound tools (open_website, play_on_youtube, ...). They
                # have no server-side effect, so this ack is the only evidence
                # that anything happened at all.
                action_id = str(pending.get("action_id") or "")
                if action_id:
                    self._dispatch_results[action_id] = {
                        "attempted": bool(attempted), "accepted": bool(accepted),
                        "error": error[:160] if error else "",
                        "tool": pending.get("tool", ""), "time": time.time(),
                    }
                    while len(self._dispatch_results) > 200:
                        self._dispatch_results.popitem(last=False)
        if pending is None:
            return False
        self._recent.append({
            "time": time.time(), "kind": "frontend_ack",
            "tool": pending.get("tool", ""),
            "dispatch_id": dispatch_id,
            "attempted": attempted, "accepted": accepted,
            "error": error[:120] if error else "",
        })
        logger.info("[PHASE4] frontend ack: dispatch=%s attempted=%s accepted=%s",
                    dispatch_id[:12], attempted, accepted)
        return True

    def dispatch_result(self, action_id: str) -> Optional[dict]:
        """Acknowledgement outcome for one dispatched frontend action, if any."""
        if not action_id:
            return None
        with self._dispatch_lock:
            return self._dispatch_results.get(str(action_id))

    def pending_dispatches(self, max_age: float = 30.0) -> list:
        """Return dispatches that haven't been acknowledged within max_age."""
        cutoff = time.time() - max_age
        with self._dispatch_lock:
            self._sweep_pending_locked()
            return [d for d in self._pending_dispatches.values()
                    if float(d.get("dispatched_at", 0)) >= cutoff]

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
