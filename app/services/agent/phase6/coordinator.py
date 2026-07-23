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
from app.services.agent.phase6.command_cache import (
    CommandCache, KIND_TOOL, KIND_PLAN,
)

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
                    from app.services.agent.phase4.event_bus import get_event_bus
                    self.bus = get_event_bus()
                if self.bus is not None:
                    self.bus.subscribe("verified", self._on_verified)
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
            from app.services.agent.phase4 import models
            return models.PASS, models.FAIL
        except Exception:  # noqa: BLE001
            return "PASS", "FAIL"

    # -- promote / evict on verification --------------------------------- #
    def _on_verified(self, payload: dict) -> None:
        """Bus handler: promote on PASS, evict on FAIL. Never raises."""
        try:
            if not payload or self.cache is None:
                return
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

    # -- read path for the chat layer ------------------------------------ #
    def lookup(self, command: Any) -> Optional[Dict[str, Any]]:
        """Return a safe, verified exact-match cache entry, or None.
        Referential commands are never served from cache."""
        if not self.started or self.cache is None:
            return None
        try:
            if self._is_referential(command):
                return None
            entry = self.cache.get(command)
            if entry:
                self._bump("hits")
                self.cache.record_hit(command)
                self._note("hit", CommandCache.normalize(command), entry.get("kind", ""))
            else:
                self._bump("misses")
            return entry
        except Exception as e:  # noqa: BLE001
            logger.debug("[CACHE] lookup failed: %s", e)
            return None

    def invalidate(self, command: Any) -> None:
        """Manually drop a command from the fast path (e.g. user said it's wrong)."""
        if self.cache is None:
            return
        try:
            if self.cache.get(command) is not None:
                self.cache.evict(command)
                self._bump("evictions")
                self._note("evict", CommandCache.normalize(command), "manual")
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
