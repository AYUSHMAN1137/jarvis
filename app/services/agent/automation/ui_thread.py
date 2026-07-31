"""Dedicated UI-automation thread -- the single owner of all COM/UIA objects.

WHY THIS EXISTS (the bug it fixes)
----------------------------------
Windows UI Automation is COM. A COM object belongs to the apartment of the
thread that created it. If another thread calls that object, COM must marshal
the call back to the owning thread and wait for that thread to service it.

J.A.R.V.I.S used to create `Desktop(backend="uia")` on the main thread at
startup, then call it from uvicorn's request threads. The main thread runs the
asyncio event loop and never pumps COM messages, so those marshalled calls sat
in the queue forever. Observed effect in production logs: every single UI call
from a request timed out (`best=0.00`, no controls scored at all), while the
identical tree walk completed in 141 ms once the queue happened to drain --
minutes later, with `elapsed_ms=305824`. 9 out of 9 `ui_*` calls failed.

THE DESIGN
----------
One long-lived thread owns everything:

  * It initialises a single-threaded apartment (STA) -- what UIA expects.
  * It creates every COM object it uses, so no call ever crosses an apartment.
  * It pumps Windows messages while idle, so COM/UIA callbacks are serviced.
  * All UI work is submitted to it as jobs; callers wait with a real timeout.

Consequences that matter:
  * No marshalling, therefore no deadlock.
  * Access is serialised, so two chat turns cannot corrupt each other's walk.
  * Timeouts are honest: the caller is released even when an app is wedged.
  * If a job does wedge the thread (a frozen app can do this), the thread is
    retired and a fresh one takes over. Callers keep working. Owners of COM
    objects from the retired apartment must rebuild them, which is what the
    `generation` counter is for.

This module deliberately knows nothing about pywinauto. It is a generic
"run this callable on the UI apartment" service.
"""

from __future__ import annotations

import ctypes
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Tuple

logger = logging.getLogger("J.A.R.V.I.S")

# Windows message-pump constants
_PM_REMOVE = 0x0001
_WM_QUIT = 0x0012

# COM apartment flags (from objbase.h)
_COINIT_APARTMENTTHREADED = 0x2
_RPC_E_CHANGED_MODE = -2147417850

# Max number of times we will retire a wedged thread before giving up. A wedged
# thread leaks (it is stuck inside a COM call we cannot cancel), so this is
# bounded on purpose.
_MAX_GENERATIONS = 8


@dataclass
class UIResult:
    """Outcome of a job submitted to the UI thread. Never raises on its own."""
    value: Any = None
    error: Optional[BaseException] = None
    timed_out: bool = False
    elapsed_ms: float = 0.0
    generation: int = 0

    @property
    def ok(self) -> bool:
        return not self.timed_out and self.error is None


@dataclass
class _Job:
    fn: Callable[[], Any]
    label: str
    done: threading.Event = field(default_factory=threading.Event)
    value: Any = None
    error: Optional[BaseException] = None
    abandoned: bool = False


class UIThread:
    """Serialised executor pinned to one COM (STA) apartment."""

    def __init__(self, name: str = "uia-owner") -> None:
        self._name = name
        self._lock = threading.RLock()
        self._queue: Optional[queue.Queue] = None
        self._worker: Optional[threading.Thread] = None
        self._generation = 0
        self._stopping = False
        self._apartment = "unknown"
        self._current: Optional[_Job] = None
        self._current_started = 0.0
        self._retired = 0
        # Identity of the owning thread, so nested submits can be detected.
        self._worker_ident: Optional[int] = None

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #
    @property
    def generation(self) -> int:
        """Increments whenever the apartment is replaced. COM objects created
        in an earlier generation are dead and must be rebuilt."""
        return self._generation

    @property
    def apartment(self) -> str:
        return self._apartment

    def start(self) -> None:
        """Start the owner thread. Idempotent and safe to call from anywhere."""
        with self._lock:
            self._ensure_worker_locked()

    def is_alive(self) -> bool:
        w = self._worker
        return bool(w is not None and w.is_alive())

    def submit(self, fn: Callable[[], Any], timeout: float = 12.0,
               label: str = "job") -> UIResult:
        """Run `fn` on the UI apartment and wait up to `timeout` seconds.

        Never raises: failures come back on the result object. A timeout retires
        the apartment, because a thread stuck inside an uncancellable COM call
        can never be reused.
        """
        started = time.perf_counter()

        # Re-entrancy: a job that submits another job would wait forever, because
        # the only thread able to service it is the one already blocked. Run it
        # inline instead -- we are already in the right apartment.
        if threading.get_ident() == self._worker_ident:
            try:
                value = fn()
                error = None
            except BaseException as exc:  # noqa: BLE001
                value, error = None, exc
            return UIResult(value=value, error=error,
                            elapsed_ms=(time.perf_counter() - started) * 1000.0,
                            generation=self._generation)

        with self._lock:
            if self._stopping:
                return UIResult(error=RuntimeError("UI thread is shutting down"),
                                generation=self._generation)
            worker_queue = self._ensure_worker_locked()
            generation = self._generation
            if worker_queue is None:
                return UIResult(
                    error=RuntimeError("UI thread unavailable (too many wedged apartments)"),
                    generation=generation,
                )

        job = _Job(fn=fn, label=label)
        worker_queue.put(job)

        if not job.done.wait(timeout=max(0.1, float(timeout))):
            job.abandoned = True
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            logger.warning("[UI-THREAD] '%s' exceeded %.1fs -- retiring apartment gen=%d",
                           label, timeout, generation)
            self._retire(generation, reason=f"'{label}' timed out after {timeout:.1f}s")
            return UIResult(timed_out=True, elapsed_ms=elapsed_ms, generation=generation)

        return UIResult(
            value=job.value, error=job.error, timed_out=False,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            generation=generation,
        )

    def stop(self, join_timeout: float = 2.0) -> None:
        """Ask the owner thread to exit. Best effort; never blocks for long."""
        with self._lock:
            self._stopping = True
            worker_queue, worker = self._queue, self._worker
            self._queue, self._worker = None, None
        if worker_queue is not None:
            worker_queue.put(None)  # sentinel
        if worker is not None and worker.is_alive():
            worker.join(timeout=join_timeout)

    def status(self) -> dict:
        """Diagnostics for the dashboard / debug log."""
        with self._lock:
            busy_for = 0.0
            if self._current is not None and self._current_started:
                busy_for = round(time.perf_counter() - self._current_started, 3)
            return {
                "alive": self.is_alive(),
                "apartment": self._apartment,
                "generation": self._generation,
                "retired": self._retired,
                "busy_with": self._current.label if self._current else "",
                "busy_for_s": busy_for,
                "queued": self._queue.qsize() if self._queue is not None else 0,
            }

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #
    def _ensure_worker_locked(self) -> Optional[queue.Queue]:
        """Caller must hold self._lock."""
        if self._worker is not None and self._worker.is_alive():
            return self._queue
        if self._retired > _MAX_GENERATIONS:
            return None

        self._generation += 1
        gen = self._generation
        # A fresh queue per apartment: jobs abandoned by a wedged apartment must
        # never be inherited by its replacement.
        worker_queue: queue.Queue = queue.Queue()
        worker = threading.Thread(
            target=self._run, args=(worker_queue, gen),
            name=f"{self._name}-g{gen}", daemon=True,
        )
        self._queue = worker_queue
        self._worker = worker
        worker.start()
        logger.info("[UI-THREAD] apartment started (generation %d)", gen)
        return worker_queue

    def _retire(self, generation: int, reason: str) -> None:
        """Abandon a wedged apartment so the next call gets a fresh one."""
        with self._lock:
            if generation != self._generation:
                return  # already replaced by someone else
            self._retired += 1
            logger.warning("[UI-THREAD] retiring apartment gen=%d: %s", generation, reason)
            # Dropping the references is enough: the stuck thread is a daemon and
            # _ensure_worker_locked() will build a replacement on the next call.
            self._queue = None
            self._worker = None
            self._apartment = "retired"

    def _run(self, worker_queue: "queue.Queue", generation: int) -> None:
        self._worker_ident = threading.get_ident()
        self._apartment = self._init_apartment()
        logger.info("[UI-THREAD] gen=%d apartment=%s ready", generation, self._apartment)
        try:
            while True:
                try:
                    job = worker_queue.get(timeout=0.05)
                except queue.Empty:
                    # Idle: service COM/UIA callbacks so the apartment stays healthy.
                    self._pump_messages()
                    if generation != self._generation:
                        return  # this apartment was retired; exit cleanly
                    continue

                if job is None:
                    return  # shutdown sentinel

                if job.abandoned:
                    # The caller already gave up. Do not run stale UI actions:
                    # clicking something a minute late is worse than not at all.
                    continue

                with self._lock:
                    self._current = job
                    self._current_started = time.perf_counter()
                try:
                    job.value = job.fn()
                except BaseException as exc:  # noqa: BLE001 - reported, never raised
                    job.error = exc
                finally:
                    with self._lock:
                        self._current = None
                        self._current_started = 0.0
                    job.done.set()
        finally:
            self._uninit_apartment()

    # -- COM apartment ------------------------------------------------- #
    @staticmethod
    def _init_apartment() -> str:
        """Enter a single-threaded apartment. UIA is STA-oriented."""
        try:
            import comtypes  # type: ignore
            try:
                comtypes.CoInitializeEx(_COINIT_APARTMENTTHREADED)
                return "sta"
            except OSError as exc:
                if getattr(exc, "winerror", None) == _RPC_E_CHANGED_MODE:
                    return "sta-existing"
                return f"oserror:{getattr(exc, 'winerror', '?')}"
        except Exception as exc:  # noqa: BLE001
            logger.debug("[UI-THREAD] comtypes init failed (%s); trying ole32", exc)
        try:
            hr = ctypes.windll.ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
            return "sta-ole32" if hr in (0, 1) else f"ole32:hr={hr}"
        except Exception as exc:  # noqa: BLE001
            logger.warning("[UI-THREAD] no COM apartment: %s", exc)
            return "none"

    @staticmethod
    def _uninit_apartment() -> None:
        try:
            ctypes.windll.ole32.CoUninitialize()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _pump_messages(max_messages: int = 25) -> None:
        """Drain the thread's message queue.

        An STA must pump messages or incoming COM calls are never dispatched.
        Bounded per idle tick so pumping can never starve real work.
        """
        try:
            user32 = ctypes.windll.user32
            msg = ctypes.wintypes.MSG()
            for _ in range(max_messages):
                if not user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, _PM_REMOVE):
                    return
                if msg.message == _WM_QUIT:
                    return
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:  # noqa: BLE001 - pumping is best effort
            return


# ctypes.wintypes is not imported by default on all builds.
try:  # pragma: no cover - import shim
    import ctypes.wintypes  # noqa: F401
except Exception:  # noqa: BLE001
    pass


# --------------------------------------------------------------------------- #
# singleton
# --------------------------------------------------------------------------- #
_ui_thread: Optional[UIThread] = None
_ui_lock = threading.Lock()


def get_ui_thread() -> UIThread:
    """Process-wide UI apartment owner."""
    global _ui_thread
    if _ui_thread is None:
        with _ui_lock:
            if _ui_thread is None:
                _ui_thread = UIThread()
                _ui_thread.start()
    return _ui_thread


def run_on_ui(fn: Callable[[], Any], timeout: float = 12.0,
              label: str = "job") -> UIResult:
    """Convenience wrapper: run `fn` on the UI apartment."""
    return get_ui_thread().submit(fn, timeout=timeout, label=label)
