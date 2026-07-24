"""Phase 6 coordinator -- wires the CommandCache into the live system.

What it does (all fail-soft; a miss/disabled cache always falls back to the
normal brain/agent path):
  * Promotes a command into the cache ONLY when the Phase 4 Checker publishes a
    `verified` PASS for it (reliability #1). Referential commands ("close it",
    "the first one") and any command touching a *dangerous* tool are never
    cached, so the confirmation gate can never be bypassed on replay.
  * Evicts a command the moment a later run is verified FAIL.
  * `lookup()` gives the chat layer a safe, instant exact-match hit (speed #2).

Nothing about any specific command is hardcoded -- every entry is learned at
runtime from a real, verified execution.

Dependencies (event bus, tool registry, context engine, phase4 models) are all
imported lazily so a missing piece never breaks startup, and can be injected
for unit tests.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Callable, Dict, List, Optional

import config as _cfg
from app.services.agent.cache.command_cache import (
    CommandCache, KIND_TOOL, KIND_PLAN,
)
from app.services.debug_logger import dbg

logger = logging.getLogger("J.A.R.V.I.S")

_ENABLED = bool(getattr(_cfg, "PHASE6_ENABLED", True))


class Phase6Coordinator:
    """Promote/evict/lookup glue around the CommandCache."""

    def __init__(
        self,
        cache: Optional[CommandCache] = None,
        bus: Any = None,
        is_dangerous: Optional[Callable[[str], bool]] = None,
        is_referential: Optional[Callable[[str], bool]] = None,
    ) -> None:
        self.enabled = _ENABLED
        self.started = False
        self.cache: Optional[CommandCache] = None
        self.bus = None
        self._cache_override = cache
        self._bus_override = bus
        self._is_dangerous_fn = is_dangerous
        self._is_referential_fn = is_referential
        self._recent: deque = deque(maxlen=60)
        self._counters = {"hits": 0, "misses": 0, "promotions": 0, "evictions": 0}
        self._lock = threading.RLock()
        self._pending_executions: Dict[str, Dict[str, Any]] = {}
        self._started_at = time.time()

    # -- lifecycle ------------------------------------------------------- #
    def start(self) -> None:
        if not self.enabled:
            logger.info("[CACHE] Phase 6 disabled (PHASE6_ENABLED=False).")
            return
        if self.started:
            return
        try:
            self.cache = self._cache_override or CommandCache()
            # Subscribe to verification verdicts so we promote/evict automatically.
            try:
                if self._bus_override is not None:
                    self.bus = self._bus_override
                else:
                    from app.services.agent.checker.event_bus import get_event_bus
                    self.bus = get_event_bus()
                if self.bus is not None:
                    self.bus.subscribe("verified", self._on_verified)
                    self.bus.subscribe("execution.completed", self._on_execution_completed)
            except Exception as e:  # noqa: BLE001
                logger.warning("[CACHE] bus wiring failed (cache still usable): %s", e)
                self.bus = None
            self.started = True
            logger.info("[CACHE] Phase 6 online -- verified-only command cache ready.")
        except Exception as e:  # noqa: BLE001 - never block startup
            logger.warning("[CACHE] Phase 6 failed to start (non-fatal): %s", e)
            self.started = False

    def stop(self) -> None:
        # The cache is persistent SQLite; nothing to tear down. Bus is shared.
        return

    # -- safety helpers -------------------------------------------------- #
    def _is_dangerous(self, tool: Any) -> bool:
        """A command touching a dangerous tool must never be cached. On any
        uncertainty we treat it as dangerous (cautious -- reliability #1)."""
        if not tool:
            return True
        if self._is_dangerous_fn is not None:
            try:
                return bool(self._is_dangerous_fn(tool))
            except Exception:  # noqa: BLE001
                return True
        try:
            from app.services.agent.tool_registry import registry
            return bool(registry.is_dangerous(tool))
        except Exception:  # noqa: BLE001
            return True

    def _is_referential(self, text: Any) -> bool:
        """Referential commands ("close it", "the second one") depend on context
        and must never be cached/replayed. On uncertainty -> True (cautious)."""
        if self._is_referential_fn is not None:
            try:
                return bool(self._is_referential_fn(text))
            except Exception:  # noqa: BLE001
                return True
        try:
            from app.services.context.context_engine import detect_reference
            return bool(detect_reference(str(text or "")))
        except Exception:  # noqa: BLE001
            return True

    def _verdicts(self):
        try:
            from app.services.agent.checker import models
            return models.PASS, models.FAIL
        except Exception:  # noqa: BLE001
            return "PASS", "FAIL"

    # -- promote / evict on verification --------------------------------- #
    def _on_verified(self, payload: dict) -> None:
        """Bus handler: promote on PASS, evict on FAIL. Never raises."""
        try:
            if not payload or self.cache is None:
                return
            execution_id = str(payload.get("execution_id") or "")
            action_id = str(payload.get("action_id") or "")
            if execution_id and action_id:
                with self._lock:
                    bucket = self._pending_executions.setdefault(execution_id, {
                        "user_message": str(payload.get("user_message") or ""),
                        "steps": [], "expected": set(), "verdicts": {},
                        "completed": False, "ok": True, "updated": time.time(),
                    })
                    bucket["verdicts"][action_id] = payload.get("verdict")
                    bucket["updated"] = time.time()
                dbg.cache_event("verdict received", payload.get("user_message", ""), {
                    "execution_id": execution_id[:12], "action_id": action_id[:12],
                    "verdict": payload.get("verdict"), "tool": payload.get("tool"),
                })
                self._try_finalize_execution(execution_id)
                return

            # Backward compatibility for callers that publish one atomic action
            # without execution metadata. Multi-step AgentLoop runs always use
            # execution IDs and therefore cannot be promoted by this path.
            user_message = str(payload.get("user_message") or "")
            trig = CommandCache.normalize(user_message)
            if not trig:
                return
            PASS, FAIL = self._verdicts()
            verdict = payload.get("verdict")

            if verdict == FAIL:
                if self.cache.get(trig) is not None:
                    self.cache.evict(trig)
                    self._bump("evictions")
                    self._note("evict", trig, str(verdict))
                return

            if verdict != PASS or not payload.get("ok", True):
                return

            # --- safety gates before caching ---
            if self._is_referential(user_message):
                return
            steps = payload.get("steps") or []
            tools = [s.get("tool") for s in steps if isinstance(s, dict) and s.get("tool")]
            if not tools:
                return
            if any(self._is_dangerous(t) for t in tools):
                return

            if len(steps) == 1:
                s = steps[0]
                ok = self.cache.put(
                    trig, KIND_TOOL,
                    {"tool": s.get("tool"), "args": s.get("args") or {}},
                )
            else:
                ok = self.cache.put(trig, KIND_PLAN, {"steps": steps})
            if ok:
                self._bump("promotions")
                self._note("promote", trig, str(verdict))
        except Exception as e:  # noqa: BLE001 - subscriber must never raise
            logger.debug("[CACHE] _on_verified failed: %s", e)

    def _on_execution_completed(self, payload: dict) -> None:
        """Join a completed execution manifest with asynchronous verdicts."""
        try:
            execution_id = str((payload or {}).get("execution_id") or "")
            if not execution_id:
                return
            steps = [dict(s) for s in ((payload or {}).get("steps") or [])
                     if isinstance(s, dict) and s.get("action_id") and s.get("tool")]
            with self._lock:
                bucket = self._pending_executions.setdefault(execution_id, {
                    "user_message": "", "steps": [], "expected": set(),
                    "verdicts": {}, "completed": False, "ok": True,
                    "updated": time.time(),
                })
                bucket["user_message"] = str(payload.get("user_message") or "")
                bucket["steps"] = steps
                bucket["expected"] = {str(s["action_id"]) for s in steps}
                bucket["completed"] = True
                bucket["ok"] = bool(payload.get("ok", True))
                bucket["updated"] = time.time()
            dbg.cache_event("execution manifest", payload.get("user_message", ""), {
                "execution_id": execution_id[:12], "steps": len(steps),
                "tool_results_ok": bool(payload.get("ok", True)),
            })
            self._try_finalize_execution(execution_id)
            self._cleanup_pending_executions()
        except Exception as e:  # noqa: BLE001
            logger.debug("[CACHE] execution completion failed: %s", e)

    def _try_finalize_execution(self, execution_id: str) -> None:
        """Promote only when every action in a completed execution is PASS."""
        PASS, FAIL = self._verdicts()
        with self._lock:
            bucket = self._pending_executions.get(execution_id)
            if not bucket:
                return
            verdicts = dict(bucket.get("verdicts") or {})
            expected = set(bucket.get("expected") or set())
            if FAIL in verdicts.values():
                user_message = bucket.get("user_message", "")
                self._pending_executions.pop(execution_id, None)
                if user_message and self.cache and self.cache.get(user_message) is not None:
                    self.cache.evict(user_message)
                    self._bump("evictions")
                    self._note("evict", CommandCache.normalize(user_message), str(FAIL))
                    dbg.cache_event("evicted after verification failure", user_message, {
                        "execution_id": execution_id[:12],
                    })
                return
            if not bucket.get("completed"):
                return
            if not bucket.get("ok") or not expected:
                dbg.cache_event("not promoted", bucket.get("user_message", ""), {
                    "reason": "execution incomplete or no actions",
                    "execution_id": execution_id[:12],
                })
                self._pending_executions.pop(execution_id, None)
                return
            if not expected.issubset(verdicts.keys()):
                return
            if any(verdicts.get(action_id) != PASS for action_id in expected):
                dbg.cache_event("not promoted", bucket.get("user_message", ""), {
                    "reason": "one or more actions were not verified PASS",
                    "execution_id": execution_id[:12],
                })
                self._pending_executions.pop(execution_id, None)
                return
            user_message = str(bucket.get("user_message") or "")
            steps = list(bucket.get("steps") or [])
            self._pending_executions.pop(execution_id, None)

        if not user_message or self._is_referential(user_message):
            dbg.cache_event("not promoted", user_message, {"reason": "context-dependent command"})
            return
        if any(self._is_dangerous(s.get("tool")) for s in steps):
            dbg.cache_event("not promoted", user_message, {"reason": "dangerous action"})
            return
        try:
            from app.services.agent.tool_registry import registry
            clean_steps = [{"tool": s.get("tool"), "args": s.get("args") or {},
                            "tool_fingerprint": registry.schema_fingerprint(s.get("tool"))}
                           for s in steps]
        except Exception:
            clean_steps = [{"tool": s.get("tool"), "args": s.get("args") or {}}
                           for s in steps]
        if len(clean_steps) == 1:
            payload = dict(clean_steps[0])
            payload["schema_version"] = 1
            ok = self.cache.put(user_message, KIND_TOOL, payload)
        else:
            ok = self.cache.put(user_message, KIND_PLAN,
                                {"schema_version": 1, "steps": clean_steps})
        if ok:
            self._bump("promotions")
            self._note("promote", CommandCache.normalize(user_message), str(PASS))
            dbg.cache_event("promoted", user_message, {
                "kind": "tool" if len(clean_steps) == 1 else "plan",
                "steps": len(clean_steps),
            })

    def _cleanup_pending_executions(self, max_age_seconds: int = 300) -> None:
        cutoff = time.time() - max(30, int(max_age_seconds))
        with self._lock:
            stale = [key for key, value in self._pending_executions.items()
                     if float(value.get("updated", 0)) < cutoff]
            for key in stale:
                self._pending_executions.pop(key, None)

    # -- read path for the chat layer ------------------------------------ #
    def lookup(self, command: Any) -> Optional[Dict[str, Any]]:
        """Return a safe, verified exact-match cache entry, or None.
        Referential commands are never served from cache."""
        if not self.started or self.cache is None:
            return None
        try:
            if self._is_referential(command):
                dbg.cache_event("lookup skipped", str(command or ""), {
                    "reason": "context-dependent command",
                })
                return None
            entry = self.cache.get(command)
            if entry and not self._entry_compatible(entry):
                self.cache.evict(command)
                self._bump("evictions")
                self._note("evict", CommandCache.normalize(command), "tool schema changed")
                dbg.cache_event("invalidated", str(command or ""),
                                {"reason": "tool schema changed"})
                entry = None
            if entry:
                self._bump("hits")
                self.cache.record_hit(command)
                self._note("hit", CommandCache.normalize(command), entry.get("kind", ""))
                dbg.cache_event("hit", str(command or ""), {
                    "kind": entry.get("kind", ""),
                })
            else:
                self._bump("misses")
                dbg.cache_event("miss", str(command or ""))
            return entry
        except Exception as e:  # noqa: BLE001
            logger.debug("[CACHE] lookup failed: %s", e)
            return None

    @staticmethod
    def _entry_compatible(entry: Dict[str, Any]) -> bool:
        """Invalidate new-format entries when current tool contracts changed.

        Legacy entries remain readable and are re-verified on replay; newly
        promoted entries always carry fingerprints.
        """
        payload = entry.get("payload") or {}
        if not payload.get("schema_version"):
            return True
        try:
            from app.services.agent.tool_registry import registry
            steps = payload.get("steps") if entry.get("kind") == KIND_PLAN else [payload]
            for step in steps or []:
                tool = step.get("tool")
                expected = step.get("tool_fingerprint")
                if not tool or not expected or registry.schema_fingerprint(tool) != expected:
                    return False
            return True
        except Exception:
            return False

    def invalidate(self, command: Any) -> None:
        """Manually drop a command from the fast path (e.g. user said it's wrong)."""
        if self.cache is None:
            return
        try:
            if self.cache.get(command) is not None:
                self.cache.evict(command)
                self._bump("evictions")
                self._note("evict", CommandCache.normalize(command), "manual")
                dbg.cache_event("invalidated", str(command or ""), {"reason": "manual/runtime failure"})
        except Exception as e:  # noqa: BLE001
            logger.debug("[CACHE] invalidate failed: %s", e)

    # -- bookkeeping ----------------------------------------------------- #
    def _bump(self, key: str) -> None:
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + 1

    def _note(self, action: str, trigger: str, detail: str = "") -> None:
        try:
            self._recent.append({
                "time": time.time(),
                "kind": "cache",
                "action": action,
                "trigger": trigger,
                "detail": detail,
            })
        except Exception:  # noqa: BLE001
            pass

    def recent_activity(self, limit: int = 30) -> List[dict]:
        try:
            items = list(self._recent)[-int(limit):]
            return list(reversed(items))
        except Exception:  # noqa: BLE001
            return []

    def health(self) -> dict:
        return {
            "enabled": self.enabled,
            "started": self.started,
            "uptime_seconds": int(max(0, time.time() - self._started_at)),
            "cache": bool(getattr(self.cache, "enabled", False)) if self.cache else False,
            "bus": self.bus is not None,
        }

    def stats(self) -> dict:
        out = {"enabled": self.enabled, "started": self.started}
        with self._lock:
            out.update(self._counters)
        try:
            if self.cache is not None:
                out["store"] = self.cache.stats()
                hits = out.get("hits", 0)
                misses = out.get("misses", 0)
                total = hits + misses
                out["hit_rate"] = round(hits / total, 3) if total else 0.0
        except Exception:  # noqa: BLE001
            pass
        return out

    def list_entries(self, limit: int = 50) -> List[dict]:
        if self.cache is None:
            return []
        return self.cache.list_entries(limit=limit)


# --------------------------------------------------------------------------- #
# singleton
# --------------------------------------------------------------------------- #
_coord: Optional[Phase6Coordinator] = None
_coord_lock = threading.Lock()


def get_phase6() -> Phase6Coordinator:
    global _coord
    if _coord is None:
        with _coord_lock:
            if _coord is None:
                _coord = Phase6Coordinator()
    return _coord
