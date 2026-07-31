"""
System State Watcher — background daemon (Phase 1 of the JARVIS smartness plan).

What it does
------------
* Runs in a background thread, refreshing every few seconds (fail-safe: a bad
  tick never crashes the app).
* Tracks a live snapshot of running processes + open windows + the active window.
* Remembers the REAL process(es) that JARVIS launched ("launched registry"), so
  "close it" / "close settings" can terminate the exact PID that was opened —
  even for UWP/Store apps (Settings -> SystemSettings.exe) and unknown apps,
  with NO hardcoded launch-name -> process-name map.

Design notes
------------
* psutil / pygetwindow are imported safely. If psutil is missing the watcher
  degrades to a no-op and callers fall back to their legacy behaviour.
* This is the foundation other components (Memory, Context resolver, Checker)
  will read from later via get_state().
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Dict, List, Optional, Set

logger = logging.getLogger("J.A.R.V.I.S")

# psutil is the core dependency for process monitoring. Imported safely so the
# server still boots if it's somehow unavailable; the watcher just degrades.
try:
    import psutil
    _PSUTIL_OK = True
except Exception as _e:  # noqa: BLE001
    _PSUTIL_OK = False
    logger.warning("[WATCHER] psutil unavailable, watcher degraded: %s", _e)


# Helper processes spawned by `start "" ...` via the shell. We don't want to
# treat these as "the app the user opened" (unless the user literally asked for
# them).
_TRANSIENT = {"cmd.exe", "conhost.exe", "start.exe"}

# Words that mean "the app I just opened" rather than a specific app name.
_PRONOUNS = {
    "", "it", "this", "that", "current", "active", "foreground",
    "isko", "ise", "is", "ye", "yah", "wo", "woh", "usko", "use", "uska",
}


class LaunchedApp:
    """One app that JARVIS launched, with the real process group behind it."""

    __slots__ = ("name", "pids", "exe", "opened_at")

    def __init__(self, name: str, pids: Set[int], exe: str = ""):
        self.name = name
        self.pids: Set[int] = set(pids)
        self.exe = exe
        self.opened_at = time.time()


class SystemStateWatcher:
    """Background daemon maintaining live system state."""

    def __init__(self, interval: float = 2.0):
        self._interval = interval
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        # live state
        self._procs: Dict[int, Dict] = {}     # pid -> {pid, name, exe, created}
        self._windows: List[str] = []         # window titles
        self._windows_detail: List[Dict] = []  # [{title,left,top,width,height,active}]
        self._active_window: str = ""
        # clipboard + settings (toggles) live snapshot
        self._clipboard_preview: str = ""
        # Recent clipboard entries, newest first. Fed by the existing tick --
        # deliberately NOT a second poller. RAM only, never logged, never
        # written to disk: the clipboard can hold a password.
        self._clipboard_history: List[str] = []
        self._clipboard_history_max: int = 20
        self._settings: Dict = {}
        self._tick: int = 0
        # settings are heavier to read, so refresh them every Nth tick only
        self._settings_every = 5
        # apps JARVIS launched, most-recent LAST
        self._launched: List[LaunchedApp] = []
        # Phase 7: previous snapshot for change-detection + lazy bus/diff refs.
        # Event emission is PURE (diffs already-collected state) and never
        # touches COM, so it can't re-trigger the cross-thread COM crash.
        self._prev_state: Optional[dict] = None
        self._emit_ready: Optional[bool] = None
        self._diff_fn = None
        self._bus = None

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if not _PSUTIL_OK:
            logger.warning("[WATCHER] not starting (psutil missing).")
            return
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="jarvis-watcher", daemon=True
            )
            self._thread.start()
            logger.info(
                "[WATCHER] Background daemon started (interval=%.1fs).",
                self._interval,
            )

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=2.0)
        logger.info("[WATCHER] Background daemon stopped.")

    # ------------------------------------------------------------------ #
    # main loop
    # ------------------------------------------------------------------ #
    def _run(self) -> None:
        self._refresh()  # prime immediately so state is ready fast
        while not self._stop.wait(self._interval):
            self._refresh()

    def _refresh(self) -> None:
        # A single bad tick must never kill the daemon.
        try:
            self._refresh_procs()
        except Exception as e:  # noqa: BLE001
            logger.debug("[WATCHER] proc refresh error: %s", e)
        try:
            self._refresh_windows()
        except Exception as e:  # noqa: BLE001
            logger.debug("[WATCHER] window refresh error: %s", e)
        try:
            self._refresh_clipboard()
        except Exception as e:  # noqa: BLE001
            logger.debug("[WATCHER] clipboard refresh error: %s", e)
        try:
            # Settings (volume/brightness/wifi/bluetooth) are heavier -> throttle.
            if self._tick % self._settings_every == 0:
                self._refresh_settings()
        except Exception as e:  # noqa: BLE001
            logger.debug("[WATCHER] settings refresh error: %s", e)
        try:
            self._prune_launched()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._emit_events()
        except Exception as e:  # noqa: BLE001 - emission must never crash the daemon
            logger.debug("[WATCHER] event emit error: %s", e)
        self._tick += 1

    def _emit_events(self) -> None:
        """Phase 7: publish system-state-change events on the event bus.

        Pure + fail-soft: it only diffs two Python snapshots (no psutil call,
        no COM, no blocking) and hands each change to the bus, which dispatches
        on its own thread pool. Disabled cleanly if Phase 7 is off or the bus
        is unavailable -- in which case the watcher behaves exactly as before.
        """
        if self._emit_ready is None:
            try:
                import config as _cfg
                if not bool(getattr(_cfg, "PHASE7_ENABLED", True)):
                    self._emit_ready = False
                    return
                from app.services.agent.proactive.events import diff_state
                from app.services.agent.checker.event_bus import get_event_bus
                self._diff_fn = diff_state
                self._bus = get_event_bus()
                self._emit_ready = bool(self._bus is not None)
            except Exception as e:  # noqa: BLE001
                self._emit_ready = False
                logger.debug("[WATCHER] event emission unavailable: %s", e)
                return
        if not self._emit_ready:
            return
        try:
            curr = self.get_state()
            events = self._diff_fn(self._prev_state, curr)
            self._prev_state = curr
            for event_type, payload in events:
                try:
                    self._bus.publish(event_type, payload)
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001
            logger.debug("[WATCHER] emit diff error: %s", e)

    def _refresh_procs(self) -> None:
        procs: Dict[int, Dict] = {}
        for p in psutil.process_iter(["pid", "name", "create_time", "exe"]):
            try:
                info = p.info
                procs[info["pid"]] = {
                    "pid": info["pid"],
                    "name": (info.get("name") or "").lower(),
                    "exe": info.get("exe") or "",
                    "created": info.get("create_time") or 0.0,
                }
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        with self._lock:
            self._procs = procs

    def _refresh_windows(self) -> None:
        try:
            import pygetwindow as gw
        except Exception:  # noqa: BLE001
            return
        titles: List[str] = []
        details: List[Dict] = []
        active = ""
        try:
            aw = gw.getActiveWindow()
            if aw is not None:
                active = (getattr(aw, "title", "") or "").strip()
        except Exception:  # noqa: BLE001
            pass
        for w in gw.getAllWindows():
            t = (getattr(w, "title", "") or "").strip()
            if not t:
                continue
            titles.append(t)
            try:
                details.append({
                    "title": t,
                    "left": int(getattr(w, "left", 0) or 0),
                    "top": int(getattr(w, "top", 0) or 0),
                    "width": int(getattr(w, "width", 0) or 0),
                    "height": int(getattr(w, "height", 0) or 0),
                    "active": bool(active and t == active),
                })
            except Exception:  # noqa: BLE001
                details.append({"title": t, "active": bool(active and t == active)})
        with self._lock:
            self._windows = titles
            self._windows_detail = details
            self._active_window = active

    def _refresh_clipboard(self) -> None:
        """Snapshot a short preview of the clipboard text. Privacy: kept in RAM
        only, capped, and NEVER logged (could contain a password)."""
        try:
            import pyperclip
        except Exception:  # noqa: BLE001
            return
        try:
            text = pyperclip.paste() or ""
        except Exception:  # noqa: BLE001
            return
        text = str(text).replace("\r", " ").replace("\n", " ").strip()
        preview = text[:200]
        with self._lock:
            self._clipboard_preview = preview
            # Ring buffer of distinct entries. Re-copying the same thing must not
            # push everything else out.
            if preview and (not self._clipboard_history
                            or self._clipboard_history[0] != preview):
                self._clipboard_history.insert(0, preview)
                del self._clipboard_history[self._clipboard_history_max:]

    def clipboard_history(self, limit: int = 20) -> List[str]:
        """Recent distinct clipboard entries, newest first. RAM only."""
        with self._lock:
            return list(self._clipboard_history[:max(1, int(limit))])

    def _refresh_settings(self) -> None:
        """Snapshot toggle state (volume/brightness/wifi/bluetooth). Best-effort:
        a read failure just leaves the previous value."""
        try:
            from app.services.agent.tools.settings_tools import read_system_status
        except Exception:  # noqa: BLE001
            return
        try:
            # use_cache=True: never create a COM object on the 2s poll (that
            # constant churn was crashing the process). Live volume is read
            # only when the user explicitly asks.
            status = read_system_status(use_cache=True)
        except Exception:  # noqa: BLE001
            return
        if isinstance(status, dict):
            with self._lock:
                self._settings = status

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def _current_pids(self) -> Set[int]:
        if not _PSUTIL_OK:
            return set()
        try:
            return set(psutil.pids())
        except Exception:  # noqa: BLE001
            return set()

    def snapshot_pids(self) -> Set[int]:
        """Public: capture current PIDs (call BEFORE launching an app)."""
        return self._current_pids()

    def _prune_launched(self) -> None:
        if not _PSUTIL_OK:
            return
        alive = set(self._procs.keys()) if self._procs else self._current_pids()
        with self._lock:
            for app in self._launched:
                app.pids = {pid for pid in app.pids if pid in alive}
            self._launched = [a for a in self._launched if a.pids]

    # ------------------------------------------------------------------ #
    # generic name matching (no hardcoded app -> exe map)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _name_candidates(name: str) -> Set[str]:
        """Matchable fragments derived from the requested app name.

        Purely generic: split the name into tokens and add a compacted form
        (spaces/sep removed). Used to test whether a real process name
        plausibly belongs to what the user asked to open. NO app list.
        """
        name = (name or "").replace(".exe", "").strip().lower()
        if not name:
            return set()
        cands: Set[str] = set()
        for tok in name.replace("-", " ").replace("_", " ").split():
            if len(tok) >= 3:
                cands.add(tok)
        compact = name.replace(" ", "").replace("-", "").replace("_", "")
        if len(compact) >= 3:
            cands.add(compact)
        return cands

    @staticmethod
    def _name_related(pname: str, candidates: Set[str]) -> bool:
        """True if a real process name is plausibly the requested app."""
        pname = (pname or "").replace(".exe", "").strip().lower()
        if not pname or not candidates:
            return False
        for c in candidates:
            if c in pname or pname in c:
                return True
        return False

    # ------------------------------------------------------------------ #
    # launch tracking
    # ------------------------------------------------------------------ #
    def note_launch(
        self,
        requested_name: str,
        before_pids: Set[int],
        timeout: float = 2.5,
    ) -> Optional[LaunchedApp]:
        """After launching an app, discover which NEW process(es) it spawned by
        diffing against ``before_pids``. Stores them in the launched registry.
        """
        if not _PSUTIL_OK:
            return None
        name = (requested_name or "").strip().lower()
        deadline = time.time() + timeout
        new_pids: Set[int] = set()
        chosen: Dict[int, str] = {}

        def _scan() -> None:
            try:
                now = set(psutil.pids())
            except Exception:  # noqa: BLE001
                return
            for pid in now - before_pids:
                if pid in chosen:
                    continue
                try:
                    p = psutil.Process(pid)
                    pname = (p.name() or "").lower()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
                # Skip shell helper processes unless the user asked for them.
                if pname in _TRANSIENT and pname != f"{name}.exe":
                    continue
                new_pids.add(pid)
                chosen[pid] = pname

        while time.time() < deadline:
            _scan()
            if new_pids:
                # Give siblings a brief moment to appear, then finalize.
                time.sleep(0.4)
                _scan()
                break
            time.sleep(0.2)

        if not new_pids:
            logger.info("[WATCHER] launch '%s': no new process captured.", name)
            return None

        # ----------------------------------------------------------------- #
        # Conservative matching (reliability #1): keep ONLY the new process(es)
        # whose real executable name is plausibly related to what the user
        # asked to open. This stops mis-attributing stray PIDs (e.g. a random
        # git.exe) to an app that never actually launched -- not installed, or
        # opened via a launcher. No hardcoded app->exe map: we compare the
        # requested name against the actual process name generically.
        # ----------------------------------------------------------------- #
        candidates = self._name_candidates(name)
        related = {
            pid for pid in new_pids
            if self._name_related(chosen.get(pid, ""), candidates)
        }

        if not related:
            logger.info(
                "[WATCHER] launch '%s': new proc(s) %s unrelated to request "
                "-> tracking nothing (safe).",
                name, sorted({chosen.get(p, "") for p in new_pids}),
            )
            return None

        exe = chosen[sorted(related)[0]]
        app = LaunchedApp(name=name, pids=related, exe=exe)
        with self._lock:
            self._launched.append(app)
        logger.info(
            "[WATCHER] launch '%s' -> pid(s)=%s exe=%s",
            name, sorted(related), exe,
        )
        return app

    # ------------------------------------------------------------------ #
    # closing
    # ------------------------------------------------------------------ #
    def _kill_pids(self, pids: Set[int]) -> List[int]:
        killed: List[int] = []
        for pid in list(pids):
            try:
                p = psutil.Process(pid)
            except psutil.NoSuchProcess:
                continue
            except Exception:  # noqa: BLE001
                continue
            try:
                for child in p.children(recursive=True):
                    try:
                        child.kill()
                    except Exception:  # noqa: BLE001
                        pass
                p.kill()
                killed.append(pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                logger.debug("[WATCHER] kill %s failed: %s", pid, e)
        return killed

    def _find_pids_by_name(self, name: str) -> Set[int]:
        name = name.replace(".exe", "").strip().lower()
        if not name:
            return set()
        found: Set[int] = set()
        with self._lock:
            procs = list(self._procs.values())
        if not procs:
            # Live fallback scan if the daemon hasn't primed yet.
            try:
                for p in psutil.process_iter(["pid", "name"]):
                    pn = (p.info.get("name") or "").lower().replace(".exe", "")
                    if pn and (name in pn or pn in name):
                        found.add(p.info["pid"])
            except Exception:  # noqa: BLE001
                return set()
            return found
        for info in procs:
            pn = info["name"].replace(".exe", "")
            if pn and (name in pn or pn in name):
                found.add(info["pid"])
        return found

    def close_by_name(self, name: str) -> dict:
        """Resolve a close target generically and terminate it.

        Returns ``{"matched": bool, "killed": [pids], "label": str}``.
        Resolution order:
          1. pronoun / empty  -> the most recently launched app still alive
          2. name match in the launched registry (real tracked PIDs)
          3. name match against any running process
        """
        if not _PSUTIL_OK:
            return {"matched": False, "killed": [], "label": name}
        raw = (name or "").strip().lower()

        with self._lock:
            launched = list(self._launched)

        # 1) pronoun / empty -> last launched still alive
        if raw in _PRONOUNS:
            if launched:
                app = launched[-1]
                killed = self._kill_pids(app.pids)
                self._prune_launched()
                return {
                    "matched": bool(killed),
                    "killed": killed,
                    "label": app.name or "the app",
                }
            return {"matched": False, "killed": [], "label": "it"}

        # 2) named target -> close EVERY matching instance, not just the most
        #    recent one. Gather PIDs from the launched registry AND from a live
        #    by-name sweep, dedupe, then kill them all in one pass. This makes
        #    "saare notepad band karo" actually close all of them instead of
        #    leaving older instances open (which used to report a false 'done').
        target_pids: List[int] = []
        label = raw
        for app in launched:
            if app.name and (raw in app.name or app.name in raw):
                target_pids.extend(app.pids)
                label = app.name
        try:
            target_pids.extend(self._find_pids_by_name(raw))
        except Exception:  # noqa: BLE001
            pass
        # dedupe while preserving order
        seen: set = set()
        unique_pids = [p for p in target_pids if not (p in seen or seen.add(p))]
        if unique_pids:
            killed = self._kill_pids(unique_pids)
            self._prune_launched()
            return {"matched": bool(killed), "killed": killed, "label": label}

        return {"matched": False, "killed": [], "label": raw}

    # ------------------------------------------------------------------ #
    # forced refresh (defeats the throttled snapshot for verification)
    # ------------------------------------------------------------------ #
    def refresh_now(self) -> dict:
        """Force an immediate, COMPLETE refresh (procs + windows + settings) and
        return the fresh state.

        The background loop throttles the heavier settings read (every Nth
        tick), so right after a toggle the cached snapshot can be stale. The
        Checker calls this so a Wi-Fi/Bluetooth/brightness/mute change or a
        just-closed window is read at its CURRENT value instead of a false one.
        Fail-soft: any sub-read error is swallowed; we still return get_state().
        """
        if not _PSUTIL_OK:
            return self.get_state()
        try:
            self._refresh_procs()
        except Exception as e:  # noqa: BLE001
            logger.debug("[WATCHER] refresh_now proc error: %s", e)
        try:
            self._refresh_windows()
        except Exception as e:  # noqa: BLE001
            logger.debug("[WATCHER] refresh_now window error: %s", e)
        try:
            self._refresh_settings()   # force-read (NOT throttled) -- the key fix
        except Exception as e:  # noqa: BLE001
            logger.debug("[WATCHER] refresh_now settings error: %s", e)
        try:
            self._prune_launched()
        except Exception:  # noqa: BLE001
            pass
        return self.get_state()

    # ------------------------------------------------------------------ #
    # read API (for other components later)
    # ------------------------------------------------------------------ #
    def get_state(self) -> dict:
        with self._lock:
            return {
                "process_count": len(self._procs),
                "windows": list(self._windows),
                "windows_detail": list(self._windows_detail),
                "active_window": self._active_window,
                "clipboard_preview": self._clipboard_preview,
                "settings": dict(self._settings),
                "launched": [
                    {
                        "name": a.name,
                        "pids": sorted(a.pids),
                        "exe": a.exe,
                        "opened_at": a.opened_at,
                    }
                    for a in self._launched
                ],
            }

    def list_open_windows(self, limit: int = 40) -> List[str]:
        with self._lock:
            return list(self._windows)[:limit]


# --------------------------------------------------------------------------- #
# module-level singleton
# --------------------------------------------------------------------------- #
_watcher: Optional[SystemStateWatcher] = None


def get_watcher() -> SystemStateWatcher:
    global _watcher
    if _watcher is None:
        _watcher = SystemStateWatcher()
    return _watcher
