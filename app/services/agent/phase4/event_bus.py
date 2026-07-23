"""In-memory, fail-soft publish/subscribe Event Bus (Phase 4, Chunk 1).

Why a thread-backed bus (and not raw asyncio)?
----------------------------------------------
The agent ReAct loop (`agent_loop.run_stream`) is a *synchronous* generator and
the watcher is a background *thread*. Bridging those into an asyncio loop would
add fragile cross-thread coordination on the hot voice path. A tiny thread-pool
dispatcher gives us the exact same guarantees the design locked in -- in-memory,
transient, non-blocking, fail-soft -- while staying dead simple and reliable
(reliability #1).

Guarantees
----------
* publish() NEVER blocks the caller and NEVER raises. Handlers run on a small
  background pool, so a slow subscriber (e.g. a Vision call) can't stall the
  voice/agent path (speed #2).
* Each handler is isolated in try/except -- one bad subscriber can't break the
  others or the publisher.
* Transient by design: if the bus is shut down, events are dropped silently.
  The valuable state (skills / learning) lives in SQLite, not in the bus.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("J.A.R.V.I.S")

Handler = Callable[[dict], None]


class EventBus:
    """A minimal, thread-safe, fail-soft pub/sub bus."""

    def __init__(self, max_workers: int = 2) -> None:
        self._subs: Dict[str, List[Handler]] = {}
        self._lock = threading.RLock()
        self._executor: Optional[ThreadPoolExecutor] = None
        self._max_workers = max(1, int(max_workers))
        self._closed = False
        self._stats = {
            "published": 0,
            "dispatched": 0,
            "errors": 0,
            "dropped": 0,
        }

    # -- internal -------------------------------------------------------- #
    def _ensure_executor(self) -> Optional[ThreadPoolExecutor]:
        if self._executor is None and not self._closed:
            self._executor = ThreadPoolExecutor(
                max_workers=self._max_workers,
                thread_name_prefix="jarvis-bus",
            )
        return self._executor

    def _run_handler(self, handler: Handler, event_type: str, payload: dict) -> None:
        try:
            handler(payload)
            with self._lock:
                self._stats["dispatched"] += 1
        except Exception as e:  # noqa: BLE001 - a subscriber must never crash the bus
            with self._lock:
                self._stats["errors"] += 1
            logger.warning("[BUS] handler for '%s' failed: %s", event_type, str(e)[:160])

    # -- public API ------------------------------------------------------ #
    def subscribe(self, event_type: str, handler: Handler) -> None:
        """Register a handler for an event type. Safe to call any time."""
        if not event_type or not callable(handler):
            return
        with self._lock:
            self._subs.setdefault(event_type, []).append(handler)
        logger.info(
            "[BUS] subscribed '%s' -> %s",
            event_type, getattr(handler, "__qualname__", repr(handler)),
        )

    def publish(self, event_type: str, payload: Optional[dict] = None) -> None:
        """Fire an event. Returns immediately; never blocks, never raises."""
        payload = payload or {}
        try:
            with self._lock:
                if self._closed:
                    return
                handlers = list(self._subs.get(event_type, ()))
                self._stats["published"] += 1
                ex = self._ensure_executor()
            if not handlers or ex is None:
                return
            for h in handlers:
                try:
                    ex.submit(self._run_handler, h, event_type, dict(payload))
                except Exception as e:  # noqa: BLE001 - executor rejected / closing
                    with self._lock:
                        self._stats["dropped"] += 1
                    logger.debug("[BUS] dropped '%s': %s", event_type, e)
        except Exception as e:  # noqa: BLE001 - publish must never raise
            logger.debug("[BUS] publish error ('%s'): %s", event_type, e)

    def stats(self) -> dict:
        with self._lock:
            return dict(self._stats)

    def shutdown(self) -> None:
        """Stop dispatching. Idempotent and safe."""
        with self._lock:
            self._closed = True
            ex = self._executor
            self._executor = None
        if ex is not None:
            try:
                ex.shutdown(wait=False)
            except Exception:  # noqa: BLE001
                pass


# --------------------------------------------------------------------------- #
# module-level singleton
# --------------------------------------------------------------------------- #
_bus_singleton: Optional[EventBus] = None
_bus_lock = threading.Lock()


def get_event_bus() -> EventBus:
    """Process-wide EventBus singleton (never raises)."""
    global _bus_singleton
    if _bus_singleton is None:
        with _bus_lock:
            if _bus_singleton is None:
                _bus_singleton = EventBus()
    return _bus_singleton
