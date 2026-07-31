"""
Debug Logger -- real-time, high-detail session logging for J.A.R.V.I.S diagnostics.

Writes structured, human-readable logs to data/debug_logs/ so that:

  * session_<ts>_<sid8>.log  -- ONE file per chat session. Every turn of that
    session is appended to the same file with a clear TURN banner, so a whole
    test run reads top-to-bottom instead of being scattered across 30 files.
  * trace.log                -- EVERY line from EVERY session, continuous.
    This is the single file to read when diagnosing a test run. Rotated at 8 MB
    to trace.log.1 so it never bloats.
  * server.log               -- the stdlib/console logger mirror (wired in
    app/main.py), which captures third-party tracebacks and engine warnings.

Everything is flushed per line (real time, tail-able) and every writer is
exception-proof: logging must never break a request.

Usage:
    from app.services.debug_logger import dbg

    dbg.session_start(session_id, user_message)
    dbg.tool_call("ui_click", {"name": "Play"}, step=2)
    dbg.tool_result("ui_click", "Clicked 'Play'.", ok=True, duration_ms=812)
    dbg.uia_diag("find.tree", window="Chrome", descendants=1843, elapsed_ms=2140)
    dbg.uia_candidates("Play", [("Play (k)", "Button", 0.9), ...])
    dbg.session_end(session_id, "Playing Tum Hi Ho on YouTube.")
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("J.A.R.V.I.S")

# ---------------------------------------------------------------------------
# Log locations
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEBUG_LOG_DIR = _PROJECT_ROOT / "data" / "debug_logs"
TRACE_LOG = DEBUG_LOG_DIR / "trace.log"

# Keep max N per-session files (oldest deleted).
_MAX_LOG_FILES = 40
# Rotate trace.log past this size.
_TRACE_MAX_BYTES = 8 * 1024 * 1024

_WIDTH = 100


class DebugLogger:
    """Thread-safe, real-time debug logger.

    One file per SESSION (appended across turns) plus a continuous trace file.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._file = None                      # active session file handle
        self._path: Optional[Path] = None
        self._session_id: Optional[str] = None
        self._turn_index: int = 0
        self._session_started_at: Optional[float] = None
        self._turn_started_at: Optional[float] = None
        self._paths: Dict[str, Path] = {}      # session_id -> log path
        self._trace = None
        self._ensure_dir()
        self._open_trace()

    # ------------------------------------------------------------------ #
    # plumbing
    # ------------------------------------------------------------------ #
    def _ensure_dir(self) -> None:
        try:
            DEBUG_LOG_DIR.mkdir(parents=True, exist_ok=True)
        except Exception as e:  # noqa: BLE001
            logger.warning("[DEBUG-LOGGER] cannot create log dir: %s", e)

    def _open_trace(self) -> None:
        try:
            self._rotate_trace()
            self._trace = open(TRACE_LOG, "a", encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            logger.warning("[DEBUG-LOGGER] cannot open trace.log: %s", e)
            self._trace = None

    def _rotate_trace(self) -> None:
        try:
            if not TRACE_LOG.exists() or TRACE_LOG.stat().st_size < _TRACE_MAX_BYTES:
                return
            if self._trace is not None:
                self._trace.close()
                self._trace = None
            backup = TRACE_LOG.with_suffix(".log.1")
            backup.unlink(missing_ok=True)
            TRACE_LOG.rename(backup)
        except Exception:  # noqa: BLE001
            pass

    def _cleanup_old_logs(self) -> None:
        """Delete the oldest per-session files past the cap."""
        try:
            logs = sorted(
                DEBUG_LOG_DIR.glob("session_*.log"),
                key=lambda p: p.stat().st_mtime,
            )
            keep = {v for v in self._paths.values()}
            while len(logs) > _MAX_LOG_FILES:
                oldest = logs.pop(0)
                if oldest in keep:
                    continue
                oldest.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

    def _write(self, line: str) -> None:
        """Write one line to the active session file AND to trace.log."""
        try:
            if self._file is not None:
                self._file.write(line + "\n")
                self._file.flush()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._trace is not None:
                self._trace.write(line + "\n")
                self._trace.flush()
        except Exception:  # noqa: BLE001
            pass

    def _ts(self) -> str:
        return datetime.now().strftime("%H:%M:%S.%f")[:-3]

    def _elapsed(self) -> str:
        base = self._turn_started_at or self._session_started_at
        if base is None:
            return "+0.000s"
        return f"+{time.time() - base:6.3f}s"

    def _head(self) -> str:
        """Common line prefix: time, turn-elapsed, turn number."""
        turn = f"T{self._turn_index:02d}" if self._turn_index else "T--"
        return f"[{self._ts()}] [{self._elapsed()}] [{turn}]"

    def _fmt_data(self, data: Any, value_limit: int = 220) -> str:
        if data is None:
            return ""
        if isinstance(data, dict):
            parts = []
            for k, v in data.items():
                v_str = " ".join(str(v).split())
                if len(v_str) > value_limit:
                    v_str = v_str[:value_limit] + "..."
                parts.append(f"{k}={v_str}")
            return " | ".join(parts)
        s = str(data)
        if len(s) > 400:
            s = s[:400] + "..."
        return s

    # ------------------------------------------------------------------ #
    # session / turn lifecycle
    # ------------------------------------------------------------------ #
    @property
    def current_log_path(self) -> Optional[Path]:
        return self._path

    def session_start(self, session_id: str = "", user_message: str = ""):
        """Open (or re-open) the log for this session and start a new TURN.

        Unlike the old behaviour, a session_id keeps ONE file for its whole
        lifetime; each call just appends a new turn banner.
        """
        # Probe the environment BEFORE taking the lock. The probe touches other
        # subsystems, and any of them logging back into this logger while we held
        # the lock would deadlock. (It did: the UI-automation probe ran on the UI
        # thread, tried to log, and blocked until its 20s timeout.)
        env_lines = None
        if (session_id or "unknown") not in self._paths:
            env_lines = _collect_env_lines()

        with self._lock:
            sid = session_id or "unknown"
            same_session = (self._file is not None and self._session_id == sid)

            if not same_session:
                if self._file is not None:
                    self._write("")
                    self._write(f"{'=' * _WIDTH}")
                    self._write(f"  SESSION SUSPENDED ({self._session_id}) -- switching to {sid}")
                    self._write(f"{'=' * _WIDTH}")
                    try:
                        self._file.close()
                    except Exception:  # noqa: BLE001
                        pass
                    self._file = None

                self._cleanup_old_logs()
                path = self._paths.get(sid)
                fresh = path is None
                if fresh:
                    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                    path = DEBUG_LOG_DIR / f"session_{ts}_{sid[:8]}.log"
                    self._paths[sid] = path

                self._path = path
                self._session_id = sid
                self._session_started_at = time.time()
                self._turn_index = 0
                try:
                    self._file = open(path, "a", encoding="utf-8")
                except Exception as e:  # noqa: BLE001
                    logger.error("[DEBUG-LOGGER] cannot open log file: %s", e)
                    self._file = None
                    return

                if fresh:
                    self._write("=" * _WIDTH)
                    self._write("  J.A.R.V.I.S DEBUG LOG")
                    self._write(f"  Session : {sid}")
                    self._write(f"  Started : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                    self._write(f"  File    : {path}")
                    self._write(f"  Trace   : {TRACE_LOG}")
                    self._write("=" * _WIDTH)
                    self._write("")
                    self._write(f"[{self._ts()}] ENVIRONMENT")
                    for line in (env_lines or _collect_env_lines()):
                        self._write(f"    {line}")

            self._turn_index += 1
            self._turn_started_at = time.time()
            self._write("")
            self._write("=" * _WIDTH)
            self._write(
                f"  TURN {self._turn_index:02d}  |  {datetime.now().strftime('%H:%M:%S')}  |  session {sid[:8]}"
            )
            self._write(f"  USER: {' '.join(str(user_message or '').split())}")
            self._write("=" * _WIDTH)

            if self._path is not None and self._turn_index == 1:
                logger.info("[DEBUG-LOGGER] session log: %s", self._path)

    def ensure_session(self, session_id: str = "", user_message: str = ""):
        """Note a message on the active log, or start one if nothing is open."""
        with self._lock:
            if self._file is not None:
                if user_message:
                    self._write(f"{self._head()} HANDOFF -> agent | msg={' '.join(str(user_message).split())[:200]}")
                return
        self.session_start(session_id=session_id, user_message=user_message)

    def session_end(self, session_id: str = "", final_response: str = ""):
        """Close out the current TURN (the session file stays open)."""
        with self._lock:
            self._write("")
            self._write(f"{self._head()} TURN {self._turn_index:02d} END")
            if final_response:
                text = " ".join(str(final_response).split())
                self._write(f"    reply: {text[:600]}")
            self._write(f"    turn duration: {self._elapsed().strip()}")
            self._write("-" * _WIDTH)

    # ------------------------------------------------------------------ #
    # environment snapshot -- what the UI-control stack can actually do
    # ------------------------------------------------------------------ #
    def _env_snapshot_unlocked(self) -> None:
        # Collect first: probing the UIA engine emits its own log lines, and we
        # do not want those interleaved into the middle of this block.
        lines = _collect_env_lines()
        self._write("")
        self._write(f"[{self._ts()}] ENVIRONMENT")
        for line in lines:
            self._write(f"    {line}")

    def env_snapshot(self):
        with self._lock:
            self._env_snapshot_unlocked()

    def tool_catalog(self, names: Sequence[str]) -> None:
        """Record the registered tool names, highlighting the UI-control set."""
        with self._lock:
            names = list(names or [])
            ui = sorted(n for n in names if n.startswith("ui_"))
            self._write(f"{self._head()} TOOL CATALOG | total={len(names)} | ui_tools={len(ui)}")
            if ui:
                self._write(f"    ui: {', '.join(ui)}")

    # ------------------------------------------------------------------ #
    # agent / llm
    # ------------------------------------------------------------------ #
    def agent_step(self, step: int, event: str, data: Any = None):
        with self._lock:
            line = f"{self._head()} AGENT STEP {step}: {event}"
            if data:
                line += f"\n    > {self._fmt_data(data)}"
            self._write(line)

    def llm_call(self, provider: str, model: str, step: int = 0):
        with self._lock:
            self._write(f"{self._head()} LLM CALL   step={step} provider={provider}/{model}")

    def llm_response(self, provider: str, has_tool_calls: bool,
                     tool_names: Optional[list] = None, step: int = 0):
        with self._lock:
            if has_tool_calls:
                names = ", ".join(tool_names or [])
                self._write(f"{self._head()} LLM REPLY  step={step} -> tool_calls: [{names}]")
            else:
                self._write(f"{self._head()} LLM REPLY  step={step} -> final text (no tool calls)")

    def llm_text(self, text: str, step: int = 0):
        """Log the assistant's reasoning/answer text verbatim (clipped)."""
        with self._lock:
            body = " ".join(str(text or "").split())
            self._write(f"{self._head()} LLM TEXT   step={step}: {body[:800]}")

    # ------------------------------------------------------------------ #
    # tools
    # ------------------------------------------------------------------ #
    def tool_call(self, tool_name: str, args: Any = None, step: int = 0):
        with self._lock:
            line = f"{self._head()} TOOL CALL  step={step} {tool_name}"
            if args:
                line += f"\n    args: {self._fmt_data(args)}"
            self._write(line)

    def tool_result(self, tool_name: str, result: str, ok: bool = True, step: int = 0,
                    duration_ms: Optional[float] = None):
        with self._lock:
            status = "OK  " if ok else "FAIL"
            took = f" took={duration_ms:.0f}ms" if duration_ms is not None else ""
            line = f"{self._head()} TOOL {status} step={step} {tool_name}{took}"
            result_str = str(result)
            if len(result_str) > 900:
                result_str = result_str[:900] + "..."
            for rline in result_str.split("\n"):
                line += f"\n    > {rline}"
            self._write(line)

    # ------------------------------------------------------------------ #
    # generic
    # ------------------------------------------------------------------ #
    def error(self, component: str, message: str, data: Any = None,
              exc: Optional[BaseException] = None):
        with self._lock:
            line = f"{self._head()} ERROR [{component}]: {message}"
            if data:
                line += f"\n    details: {self._fmt_data(data)}"
            if exc is not None:
                tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
                for tline in tb.rstrip().split("\n")[-12:]:
                    line += f"\n    | {tline}"
            self._write(line)

    def info(self, component: str, message: str, data: Any = None):
        with self._lock:
            line = f"{self._head()} [{component}] {message}"
            if data:
                line += f"\n    > {self._fmt_data(data)}"
            self._write(line)

    def mark(self, label: str):
        """Operator-visible marker, e.g. the number of the manual test case."""
        with self._lock:
            self._write("")
            self._write(f"{self._head()} >>>>>> {label} <<<<<<")

    def execution_event(self, stage: str, execution_id: str, data: Any = None):
        short_id = str(execution_id or "-")[:12]
        self.info("EXECUTION", f"{stage.upper()} | id={short_id}", data)

    def cache_event(self, action: str, command: str = "", data: Any = None):
        message = str(action or "event").upper()
        if command:
            message += f" | command={str(command)[:160]}"
        self.info("CACHE", message, data)

    def verification_event(self, tool: str, verdict: str, reason: str = "",
                           source: str = "", execution_id: str = "",
                           action_id: str = ""):
        self.info("VERIFY", "ACTION VERDICT", {
            "tool": tool,
            "verdict": verdict,
            "reason": reason,
            "source": source,
            "execution_id": str(execution_id or "")[:12],
            "action_id": str(action_id or "")[:12],
        })

    # ------------------------------------------------------------------ #
    # UI automation -- the detailed view
    # ------------------------------------------------------------------ #
    def uia_action(self, action: str, target: str, window: str = "", result: str = "",
                   ok: bool = True):
        with self._lock:
            status = "OK  " if ok else "FAIL"
            line = f"{self._head()} UIA  {status} {action}: '{target}'"
            if window:
                line += f" in window '{window}'"
            if result:
                for rline in str(result)[:600].split("\n"):
                    line += f"\n    > {rline}"
            self._write(line)

    def uia_diag(self, event: str, **fields: Any):
        """Engine-internal diagnostics: window resolution, tree size, timings."""
        with self._lock:
            body = self._fmt_data({k: v for k, v in fields.items() if v is not None}, value_limit=160)
            line = f"{self._head()} UIA-DIAG {event}"
            if body:
                line += f" | {body}"
            self._write(line)

    def uia_candidates(self, target: str,
                       scored: Iterable[Tuple[str, str, float]],
                       shown: int = 20, total: Optional[int] = None):
        """Log the scored candidate controls -- the single most useful signal
        when a click 'fails' but the control is clearly on screen."""
        with self._lock:
            rows: List[Tuple[str, str, float]] = list(scored or [])
            count = total if total is not None else len(rows)
            self._write(
                f"{self._head()} UIA-CAND target='{' '.join(str(target or '').split())[:60]}' "
                f"| scored={count} | showing top {min(shown, len(rows))}"
            )
            for name, ctype, score in rows[:shown]:
                clean = " ".join(str(name or "").split())[:70] or "<no name>"
                self._write(f"    {score:5.2f}  {str(ctype or '?'):<14} {clean}")
            if not rows:
                self._write("    (no controls returned by the backend)")

    def uia_tree(self, window: str, items: Iterable[Dict[str, Any]], shown: int = 60):
        """Dump the enumerated control tree of a window."""
        with self._lock:
            rows = list(items or [])
            self._write(
                f"{self._head()} UIA-TREE window='{' '.join(str(window or '<active>').split())[:60]}' "
                f"| controls={len(rows)} | showing {min(shown, len(rows))}"
            )
            for it in rows[:shown]:
                tg = it.get("toggle")
                mark = "" if tg is None else (" [on]" if tg else " [off]")
                enabled = it.get("enabled")
                en = "" if enabled is None else ("" if enabled else " (disabled)")
                name = " ".join(str(it.get("name") or "").split())[:70] or "<no name>"
                self._write(f"    {str(it.get('type') or '?'):<14} {name}{mark}{en}")

    # ------------------------------------------------------------------ #
    # misc
    # ------------------------------------------------------------------ #
    def understood(self, resolution: Any):
        """M13: what the understanding layer made of this turn.

        Replaces `fast_path()`, which logged a regex match that no longer exists.
        The resolved goal is the single most useful line in a session log: when a
        turn goes wrong, it says whether JARVIS misunderstood or mis-executed.
        """
        with self._lock:
            data = resolution if isinstance(resolution, dict) else {}
            self._write(f"{self._head()} UNDERSTOOD -> {data.get('kind', '?')}"
                        f" | \"{str(data.get('goal') or '')[:160]}\"")
            self._write(f"    self_contained={data.get('self_contained')}"
                        f" refers_to_previous={data.get('refers_to_previous')}"
                        f" is_confirmation={data.get('is_confirmation')}"
                        f" source={data.get('source')}"
                        f" {data.get('elapsed_ms', 0)}ms")
            unresolved = data.get("unresolved") or []
            if unresolved:
                self._write(f"    UNRESOLVED (will ask): {unresolved}")

    def state_block(self, block: str):
        with self._lock:
            self._write(f"{self._head()} STATE BLOCK (sent to LLM):")
            for bline in (block or "").split("\n")[:24]:
                self._write(f"    {bline}")

    def raw(self, text: str):
        with self._lock:
            self._write(f"{self._head()} {text}")

    def separator(self, label: str = ""):
        with self._lock:
            if label:
                self._write(f"\n{'~' * 30} {label} {'~' * 30}")
            else:
                self._write(f"\n{'~' * _WIDTH}")


# ---------------------------------------------------------------------------
# Environment probe (used in the session header)
# ---------------------------------------------------------------------------
def _collect_env_lines() -> List[str]:
    """Report exactly which UI-automation capabilities are live. Never raises."""
    lines: List[str] = []
    import sys

    lines.append(f"python      : {sys.version.split()[0]}  ({sys.platform})")

    for mod in ("pywinauto", "comtypes", "pyautogui", "pygetwindow", "pyperclip"):
        try:
            m = __import__(mod)
            lines.append(f"{mod:<12}: {getattr(m, '__version__', 'installed')}")
        except Exception as e:  # noqa: BLE001
            lines.append(f"{mod:<12}: MISSING ({type(e).__name__})")

    try:
        import config as _cfg
        lines.append(
            f"uia config  : enabled={getattr(_cfg, 'UIA_ENABLED', None)} "
            f"library={getattr(_cfg, 'UIA_LIBRARY', None)} "
            f"find_timeout={getattr(_cfg, 'UIA_FIND_TIMEOUT', None)}"
        )
    except Exception as e:  # noqa: BLE001
        lines.append(f"uia config  : unreadable ({e})")

    try:
        # status() is a plain attribute read. Never call available() here: that
        # dispatches a job to the UI apartment, and this probe can run while the
        # caller holds a lock the UI thread needs to log through.
        from app.services.agent.automation.uia_engine import get_uia_engine
        st = get_uia_engine().status()
        lines.append(
            f"uia engine  : alive={st.get('alive')} apartment={st.get('apartment')} "
            f"generation={st.get('generation')} retired={st.get('retired')}"
            + (f" error={st.get('backend_error')}" if st.get("backend_error") else "")
        )
    except Exception as e:  # noqa: BLE001
        lines.append(f"uia engine  : probe failed ({type(e).__name__}: {e})")

    try:
        from app.services.agent.tool_registry import registry
        names = registry.names()
        lines.append(f"tools       : {len(names)} registered")
    except Exception as e:  # noqa: BLE001
        lines.append(f"tools       : probe failed ({e})")

    try:
        lines.append(f"foreground  : {foreground_window_title() or '<unknown>'}")
    except Exception:  # noqa: BLE001
        pass

    return lines


def foreground_window_title() -> str:
    """Title of the window that currently has focus. Empty string on failure."""
    try:
        import win32gui  # type: ignore
        return win32gui.GetWindowText(win32gui.GetForegroundWindow()) or ""
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_instance: Optional[DebugLogger] = None
_init_lock = threading.Lock()


def get_debug_logger() -> DebugLogger:
    global _instance
    if _instance is None:
        with _init_lock:
            if _instance is None:
                _instance = DebugLogger()
    return _instance


dbg = get_debug_logger()
