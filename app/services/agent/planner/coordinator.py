"""Phase 5 -- coordinator (singleton).

Thin glue that:
  * builds the Planner + StepExecutor on demand,
  * reuses the SHARED event bus and the Phase 4 Checker (watcher+vision verify),
  * listens to plan.* events and keeps a small rolling feed for the Control
    Center,
  * exposes health()/recent_activity()/stats() in the SAME shape Phase 4 uses,
    so the dashboard wiring stays consistent.

Fail-soft everywhere: if Phase 5 is disabled or anything is missing, run()
returns None which means "let the normal agent loop handle it".
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional

import config as _cfg

logger = logging.getLogger("J.A.R.V.I.S")


def _clean(s: Any, limit: int = 120) -> str:
    return " ".join(str(s or "").split())[:limit]


class Phase5Coordinator:
    def __init__(self) -> None:
        self.enabled = bool(getattr(_cfg, "PHASE5_ENABLED", True))
        self.planner_enabled = bool(getattr(_cfg, "PLANNER_ENABLED", True))
        self.started = False
        self.bus = None
        self._planner = None
        self._lock = threading.Lock()
        self._recent: deque = deque(maxlen=40)
        self._last_plan: Optional[Dict[str, Any]] = None
        self._started_at = time.time()
        self._counts = {"plans": 0, "steps_done": 0, "steps_failed": 0, "steps_skipped": 0}

    # -- lifecycle ------------------------------------------------------ #
    def start(self) -> None:
        if not self.enabled:
            logger.info("[PHASE5] disabled (PHASE5_ENABLED=false).")
            return
        if self.started:
            return
        try:
            from app.services.agent.checker.event_bus import get_event_bus
            # Reuse the bus Phase 4 created; if Phase 4 is off this still works.
            self.bus = get_event_bus()
            self.bus.subscribe("plan.started", self._on_plan_started)
            self.bus.subscribe("plan.step", self._on_plan_step)
            self.bus.subscribe("plan.done", self._on_plan_done)
            self.started = True
            logger.info("[PHASE5] coordinator online (planner=%s, uia=%s).",
                        self.planner_enabled, self.uia_available())
        except Exception as e:  # noqa: BLE001
            logger.warning("[PHASE5] start failed (continuing without Phase 5): %s", _clean(e))
            self.started = False

    def stop(self) -> None:
        # The bus is owned by Phase 4; we only drop our references.
        self.started = False

    # -- capability checks ---------------------------------------------- #
    def uia_available(self) -> bool:
        try:
            from app.services.agent.automation import get_uia_engine
            return bool(get_uia_engine().available())
        except Exception:  # noqa: BLE001
            return False

    def _verify_fn(self):
        """Borrow the Phase 4 checker (watcher-first + vision fallback)."""
        try:
            from app.services.agent.checker import get_phase4
            p4 = get_phase4()
            if getattr(p4, "checker", None) is not None:
                return p4.checker.verify_live
        except Exception:  # noqa: BLE001
            pass
        return None

    def _get_planner(self):
        if self._planner is None:
            from app.services.agent.planner.planner import Planner
            self._planner = Planner()
        return self._planner

    # -- main entrypoint ------------------------------------------------ #
    def run(self, goal: str, confirmed: bool = False, state: Optional[dict] = None):
        """Plan + execute a command. Returns an ExecResult, or None to signal
        'fall back to the normal agent loop'."""
        if not (self.enabled and self.planner_enabled):
            return None
        if not self.started:
            self.start()
        try:
            from app.services.agent.tool_registry import registry
            from app.services.agent.planner.executor import StepExecutor
        except Exception as e:  # noqa: BLE001
            logger.warning("[PHASE5] run unavailable: %s", _clean(e))
            return None
        try:
            plan = self._get_planner().make_plan(goal, registry, state=state)
        except Exception as e:  # noqa: BLE001
            logger.warning("[PHASE5] planning failed: %s", _clean(e))
            return None
        if plan.is_empty():
            return None
        executor = StepExecutor(registry, verify_fn=self._verify_fn(), bus=self.bus)
        try:
            return executor.run(plan, confirmed=confirmed)
        except Exception as e:  # noqa: BLE001
            logger.warning("[PHASE5] execution failed: %s", _clean(e))
            return None

    # -- event sinks (feed the dashboard) ------------------------------- #
    def _on_plan_started(self, payload: dict) -> None:
        with self._lock:
            self._counts["plans"] += 1
            self._last_plan = {"goal": _clean(payload.get("goal")),
                               "total": payload.get("total"), "source": payload.get("source"),
                               "started_at": time.time(), "steps": [], "ok": None}

    def _on_plan_step(self, payload: dict) -> None:
        with self._lock:
            status = payload.get("status")
            if status == "DONE":
                self._counts["steps_done"] += 1
            elif status == "FAILED":
                self._counts["steps_failed"] += 1
            elif status == "SKIPPED":
                self._counts["steps_skipped"] += 1
            if self._last_plan is not None:
                self._last_plan["steps"].append({
                    "index": payload.get("index"), "tool": payload.get("tool"),
                    "description": _clean(payload.get("description")), "status": status,
                    "reason": _clean(payload.get("reason")), "source": payload.get("source"),
                })

    def _on_plan_done(self, payload: dict) -> None:
        with self._lock:
            if self._last_plan is not None:
                self._last_plan["ok"] = bool(payload.get("ok"))
            self._recent.append({
                "kind": "plan", "time": time.time(),
                "goal": _clean(payload.get("goal")), "ok": bool(payload.get("ok")),
                "done": payload.get("done"), "failed": payload.get("failed"),
                "skipped": payload.get("skipped"), "summary": _clean(payload.get("summary"), 200),
            })

    # -- read API for dashboard ----------------------------------------- #
    def health(self) -> dict:
        return {
            "enabled": self.enabled,
            "started": self.started,
            "planner": self.planner_enabled,
            "uia": self.uia_available(),
            "vision_fallback": bool(getattr(_cfg, "PHASE5_VISION_FALLBACK", True)),
            "uptime_seconds": int(max(0, time.time() - self._started_at)),
        }

    def last_plan(self) -> Optional[dict]:
        with self._lock:
            return dict(self._last_plan) if self._last_plan else None

    def recent_activity(self, limit: int = 20) -> list:
        with self._lock:
            return list(self._recent)[-int(limit):][::-1]

    def stats(self) -> dict:
        with self._lock:
            return dict(self._counts)


# --------------------------------------------------------------------------- #
# singleton
# --------------------------------------------------------------------------- #
_phase5: Optional[Phase5Coordinator] = None
_phase5_lock = threading.Lock()


def get_phase5() -> Phase5Coordinator:
    global _phase5
    if _phase5 is None:
        with _phase5_lock:
            if _phase5 is None:
                _phase5 = Phase5Coordinator()
    return _phase5
