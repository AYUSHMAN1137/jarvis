"""Shared pycaw/COM helper for master-volume control.

The whole story
---------------
Volume first failed with::

    [SYSTEM] pycaw volume failed: 'AudioDevice' object has no attribute 'Activate'

Fixed by initializing COM + unwrapping pycaw's ``AudioDevice`` wrapper. But that
surfaced a SECOND, noisy crash that repeated forever::

    Exception ignored in: <function _compointer_base.__del__ ...>
    ... self.Release() ... OSError: access violation writing 0x...

Root cause: a COM interface pointer's ``Release()`` (called from ``__del__``
during garbage collection) ran on a DIFFERENT thread than the one that created
it, or after that thread's COM apartment was torn down. Our agent runs tools on
pooled worker threads and the watcher polls volume every ~2s, so pointers were
being released late, on the wrong thread / after CoUninitialize.

The robust fix (this file)
--------------------------
ALL audio COM work happens on ONE dedicated, long-lived thread
(``jarvis-audio-com``). That thread initializes COM exactly once for its entire
life and never uninitializes it during normal operation. Every set/mute/read is
shipped to that thread via a queue; the COM pointer is created, used, AND
released there, on the owning thread, while the apartment is alive. Other
threads only ever receive plain Python values (int/bool), never a live COM
pointer -- so nothing can release COM on the wrong thread. No access violation.

Everything is lazy-imported so importing this module never fails on a
non-Windows / no-pycaw environment.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Callable, Dict, Tuple, TypeVar

logger = logging.getLogger("J.A.R.V.I.S")

_T = TypeVar("_T")

# --- dedicated COM worker state (module-level singletons) ------------------- #
_jobs: "queue.Queue[Any]" = queue.Queue()
_worker: "threading.Thread | None" = None
_worker_lock = threading.Lock()
_worker_ready = threading.Event()
_worker_failed: "str | None" = None

# --- last-known volume cache -------------------------------------------------
# Lets the background watcher read volume WITHOUT creating a COM object on every
# 2s poll. That constant COM churn (combined with an MTA/STA apartment clash
# against pywinauto) was hard-crashing the whole process with no traceback.
# The cache is only refreshed on an actual on-demand COM read/set below.
_vol_cache_lock = threading.Lock()
_last_level: "int | None" = None
_last_muted: "bool | None" = None


def _update_cache(level=None, muted=None) -> None:
    """Store the most recent volume/mute we saw (from a real COM read/set)."""
    global _last_level, _last_muted
    with _vol_cache_lock:
        if level is not None:
            _last_level = int(level)
        if muted is not None:
            _last_muted = bool(muted)


def peek_volume():
    """Return the last-known (level, muted) WITHOUT touching COM.

    Values are None until the first real read/set happens. Safe to call from any
    thread (e.g. the background watcher) at zero COM cost -- this is what makes
    the 2s poll harmless.
    """
    with _vol_cache_lock:
        return _last_level, _last_muted


def _build_volume_interface():
    """Build an IAudioEndpointVolume pointer. MUST run on the COM worker thread."""
    from ctypes import cast, POINTER
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    device = AudioUtilities.GetSpeakers()

    # Newer pycaw can return a high-level AudioDevice wrapper (no .Activate)
    # instead of the raw IMMDevice. Unwrap to the underlying COM device.
    activate = getattr(device, "Activate", None)
    if activate is None:
        for attr in ("_dev", "_device", "dev", "endpoint"):
            inner = getattr(device, attr, None)
            if inner is not None and hasattr(inner, "Activate"):
                device = inner
                activate = device.Activate
                break
    if activate is None:
        raise RuntimeError(
            "audio endpoint has no Activate() (pycaw returned %r)"
            % type(device).__name__
        )

    interface = activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))


def _worker_loop() -> None:
    """Own COM for this thread's whole life and service volume jobs."""
    global _worker_failed
    try:
        import comtypes

        # Initialize COM ONCE for the life of this thread. Use STA to MATCH
        # pywinauto (which forces the process into STA). A lone MTA thread here
        # clashed with pywinauto's STA and caused an intermittent hard crash.
        # Every audio COM pointer is created, used AND released on THIS one
        # thread, so STA needs no message pump and is safe.
        try:
            comtypes.CoInitialize()  # STA (apartment-threaded)
        except Exception:  # noqa: BLE001
            comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)
    except Exception as e:  # noqa: BLE001 - no comtypes / not Windows
        _worker_failed = "COM init failed: %s" % e
        _worker_ready.set()
        return

    _worker_failed = None
    _worker_ready.set()

    while True:
        job = _jobs.get()
        if job is None:  # shutdown sentinel (not used in normal operation)
            break
        fn, fut = job
        try:
            volume = _build_volume_interface()
            try:
                fut["result"] = fn(volume)
            finally:
                # Release on THIS (owning) thread while the apartment is alive.
                del volume
        except Exception as e:  # noqa: BLE001
            fut["error"] = e
        finally:
            fut["event"].set()
    # Intentionally no CoUninitialize: the apartment lives for the process.


def _ensure_worker() -> None:
    """Start the dedicated COM worker thread if it isn't running."""
    global _worker
    with _worker_lock:
        if _worker is not None and _worker.is_alive():
            if _worker_failed:
                raise RuntimeError(_worker_failed)
            return
        _worker_ready.clear()
        _worker = threading.Thread(
            target=_worker_loop, name="jarvis-audio-com", daemon=True
        )
        _worker.start()
    if not _worker_ready.wait(timeout=5):
        raise TimeoutError("audio COM worker did not start in time")
    if _worker_failed:
        raise RuntimeError(_worker_failed)


def _run_on_com(fn: Callable[[Any], _T]) -> _T:
    """Run ``fn(volume)`` on the dedicated COM worker thread and return result."""
    _ensure_worker()
    fut: Dict[str, Any] = {"event": threading.Event()}
    _jobs.put((fn, fut))
    if not fut["event"].wait(timeout=10):
        raise TimeoutError("audio COM worker timed out")
    if "error" in fut:
        raise fut["error"]
    return fut["result"]


def set_master_volume(level_0_100: float) -> None:
    """Set master volume to a 0-100 percentage."""
    scalar = max(0.0, min(1.0, float(level_0_100) / 100.0))
    _run_on_com(lambda v: v.SetMasterVolumeLevelScalar(scalar, None))
    _update_cache(level=int(round(scalar * 100)))


def set_mute(mute: bool) -> None:
    """Mute (True) or unmute (False) the master output."""
    _run_on_com(lambda v: v.SetMute(1 if mute else 0, None))
    _update_cache(muted=bool(mute))


def read_volume() -> Tuple[int, bool]:
    """Return (level_percent:int, muted:bool) for the master output.

    This touches COM, so it must only run on an explicit user action -- NOT on a
    background poll. The watcher must use ``peek_volume()`` instead.
    """
    def _read(v):
        return int(round(v.GetMasterVolumeLevelScalar() * 100)), bool(v.GetMute())

    level, muted = _run_on_com(_read)
    _update_cache(level=level, muted=muted)
    return level, muted
