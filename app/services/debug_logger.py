"""
Debug Logger — Real-time detailed session logging for J.A.R.V.I.S diagnostics.

Writes structured, human-readable logs to data/debug_logs/ so that:
  - Every chat session gets its own log file (session_YYYY-MM-DD_HH-MM-SS.log)
  - Every agent step, tool call, tool result, error is captured with timestamps
  - Logs are flushed immediately (real-time, not buffered)
  - You can open the latest .log file and see EXACTLY what happened

Usage:
    from app.services.debug_logger import dbg

    dbg.session_start(session_id, user_message)
    dbg.agent_step(step_num, "LLM called tool: ui_click", {"name": "Play"})
    dbg.tool_result("ui_click", "Clicked 'Play'.", ok=True)
    dbg.error("uia_engine", "Control not found", {"name": "Play", "window": "Chrome"})
    dbg.session_end(session_id, "Playing Tum Hi Ho on YouTube.")
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("J.A.R.V.I.S")

# ---------------------------------------------------------------------------
# Log directory
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # jarvis-2-clean/
DEBUG_LOG_DIR = _PROJECT_ROOT / "data" / "debug_logs"

# Keep max N log files to avoid disk bloat (oldest deleted)
_MAX_LOG_FILES = 50


class DebugLogger:
    """Thread-safe, real-time debug logger that writes to per-session files."""

    def __init__(self):
        self._lock = threading.Lock()
        self._file = None
        self._path: Optional[Path] = None
        self._session_id: Optional[str] = None
        self._start_time: Optional[float] = None
        self._ensure_dir()

    def _ensure_dir(self):
        try:
            DEBUG_LOG_DIR.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning("[DEBUG-LOGGER] Cannot create log dir: %s", e)

    def _cleanup_old_logs(self):
        """Delete oldest logs if we exceed _MAX_LOG_FILES."""
        try:
            logs = sorted(DEBUG_LOG_DIR.glob("session_*.log"), key=lambda p: p.stat().st_mtime)
            while len(logs) > _MAX_LOG_FILES:
                oldest = logs.pop(0)
                oldest.unlink(missing_ok=True)
        except Exception:
            pass

    def _write(self, line: str):
        """Write a line and flush immediately."""
        if self._file is None:
            return
        try:
            self._file.write(line + "\n")
            self._file.flush()
        except Exception:
            pass

    def _ts(self) -> str:
        return datetime.now().strftime("%H:%M:%S.%f")[:-3]

    def _elapsed(self) -> str:
        if self._start_time is None:
            return "+0.000s"
        return f"+{time.time() - self._start_time:.3f}s"

    def _fmt_data(self, data: Any) -> str:
        """Format arbitrary data for log readability."""
        if data is None:
            return ""
        if isinstance(data, dict):
            parts = []
            for k, v in data.items():
                v_str = str(v)
                if len(v_str) > 200:
                    v_str = v_str[:200] + "..."
                parts.append(f"{k}={v_str}")
            return " | ".join(parts)
        s = str(data)
        if len(s) > 300:
            s = s[:300] + "..."
        return s

    # ------------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------------

    @property
    def current_log_path(self) -> Optional[Path]:
        return self._path

    def session_start(self, session_id: str = "", user_message: str = ""):
        """Start a new session log file."""
        with self._lock:
            # Close previous if open
            if self._file:
                self._write(f"\n{'='*80}")
                self._write(f"  SESSION ENDED (new session starting)")
                self._write(f"{'='*80}\n")
                self._file.close()

            self._cleanup_old_logs()

            ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            short_id = (session_id or "unknown")[:8]
            filename = f"session_{ts}_{short_id}.log"
            self._path = DEBUG_LOG_DIR / filename
            self._session_id = session_id
            self._start_time = time.time()

            try:
                self._file = open(self._path, "a", encoding="utf-8")
            except Exception as e:
                logger.error("[DEBUG-LOGGER] Cannot open log file: %s", e)
                self._file = None
                return

            self._write("=" * 80)
            self._write(f"  J.A.R.V.I.S DEBUG LOG")
            self._write(f"  Session: {session_id}")
            self._write(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            self._write(f"  File:    {self._path}")
            self._write("=" * 80)
            self._write("")
            if user_message:
                self._write(f"[{self._ts()}] USER MESSAGE: {user_message}")
                self._write("")

            logger.info("[DEBUG-LOGGER] Session log started: %s", self._path)

    def ensure_session(self, session_id: str = "", user_message: str = ""):
        """Start a session log ONLY if one isn't already active.
        Used by agent_loop as a fallback when chat_service didn't start one."""
        with self._lock:
            if self._file is not None:
                # Already logging — just note the new message
                if user_message:
                    self._write(f"[{self._ts()}] [{self._elapsed()}] NEW MESSAGE: {user_message}")
                    self._write("")
                return
        # No active session — start one
        self.session_start(session_id=session_id, user_message=user_message)

    def session_end(self, session_id: str = "", final_response: str = ""):
        """Mark end of session."""
        with self._lock:
            self._write("")
            self._write(f"[{self._ts()}] [{self._elapsed()}] SESSION END")
            if final_response:
                self._write(f"  Final response: {final_response[:500]}")
            self._write(f"  Total duration: {self._elapsed()}")
            self._write("-" * 80)
            self._write("")
            # Don't close — might get more messages in same session

    def agent_step(self, step: int, event: str, data: Any = None):
        """Log an agent loop step."""
        with self._lock:
            line = f"[{self._ts()}] [{self._elapsed()}] AGENT STEP {step}: {event}"
            if data:
                line += f"\n    > {self._fmt_data(data)}"
            self._write(line)

    def tool_call(self, tool_name: str, args: Any = None, step: int = 0):
        """Log a tool being called."""
        with self._lock:
            line = f"[{self._ts()}] [{self._elapsed()}] TOOL CALL: {tool_name}"
            if step:
                line += f" (step {step})"
            if args:
                line += f"\n    Args: {self._fmt_data(args)}"
            self._write(line)

    def tool_result(self, tool_name: str, result: str, ok: bool = True, step: int = 0):
        """Log a tool result."""
        with self._lock:
            status = "OK" if ok else "FAILED"
            line = f"[{self._ts()}] [{self._elapsed()}] TOOL RESULT [{status}]: {tool_name}"
            if step:
                line += f" (step {step})"
            result_str = str(result)
            if len(result_str) > 500:
                result_str = result_str[:500] + "..."
            line += f"\n    > {result_str}"
            self._write(line)

    def llm_call(self, provider: str, model: str, step: int = 0):
        """Log an LLM API call."""
        with self._lock:
            self._write(
                f"[{self._ts()}] [{self._elapsed()}] LLM CALL: {provider}/{model} (step {step})"
            )

    def llm_response(self, provider: str, has_tool_calls: bool, tool_names: list = None, step: int = 0):
        """Log what the LLM responded with."""
        with self._lock:
            if has_tool_calls:
                names = ", ".join(tool_names or [])
                self._write(
                    f"[{self._ts()}] [{self._elapsed()}] LLM RESPONSE: {provider} -> tool_calls: [{names}]"
                )
            else:
                self._write(
                    f"[{self._ts()}] [{self._elapsed()}] LLM RESPONSE: {provider} -> final text (no more tools)"
                )

    def error(self, component: str, message: str, data: Any = None):
        """Log an error."""
        with self._lock:
            line = f"[{self._ts()}] [{self._elapsed()}] ERROR [{component}]: {message}"
            if data:
                line += f"\n    Details: {self._fmt_data(data)}"
            self._write(line)

    def info(self, component: str, message: str, data: Any = None):
        """Log an informational event."""
        with self._lock:
            line = f"[{self._ts()}] [{self._elapsed()}] [{component}] {message}"
            if data:
                line += f"\n    > {self._fmt_data(data)}"
            self._write(line)

    def execution_event(self, stage: str, execution_id: str, data: Any = None):
        """Log one high-value execution lifecycle event."""
        short_id = str(execution_id or "-")[:12]
        self.info("EXECUTION", f"{stage.upper()} | id={short_id}", data)

    def cache_event(self, action: str, command: str = "", data: Any = None):
        """Log cache decisions without dumping excessive internal state."""
        message = str(action or "event").upper()
        if command:
            message += f" | command={str(command)[:160]}"
        self.info("CACHE", message, data)

    def verification_event(self, tool: str, verdict: str, reason: str = "",
                           source: str = "", execution_id: str = "",
                           action_id: str = ""):
        """Log the final checker verdict with trace IDs and evidence source."""
        data = {
            "tool": tool,
            "verdict": verdict,
            "reason": reason,
            "source": source,
            "execution_id": str(execution_id or "")[:12],
            "action_id": str(action_id or "")[:12],
        }
        self.info("VERIFY", "ACTION VERDICT", data)

    def uia_action(self, action: str, target: str, window: str = "", result: str = "", ok: bool = True):
        """Log a UIA (pywinauto) action specifically."""
        with self._lock:
            status = "OK" if ok else "FAIL"
            line = f"[{self._ts()}] [{self._elapsed()}] UIA [{status}] {action}: '{target}'"
            if window:
                line += f" in window '{window}'"
            if result:
                line += f"\n    > {result[:300]}"
            self._write(line)

    def fast_path(self, matched: bool, tool: str = "", args: Any = None):
        """Log fast-path (regex) matching."""
        with self._lock:
            if matched:
                line = f"[{self._ts()}] [{self._elapsed()}] FAST-PATH MATCH: {tool}"
                if args:
                    line += f"\n    Args: {self._fmt_data(args)}"
            else:
                line = f"[{self._ts()}] [{self._elapsed()}] FAST-PATH: no match -> sending to LLM agent"
            self._write(line)

    def state_block(self, block: str):
        """Log the context/state block sent to the LLM."""
        with self._lock:
            self._write(f"[{self._ts()}] [{self._elapsed()}] STATE BLOCK (sent to LLM):")
            for bline in (block or "").split("\n")[:20]:
                self._write(f"    {bline}")
            self._write("")

    def raw(self, text: str):
        """Write a raw line (for custom logging)."""
        with self._lock:
            self._write(f"[{self._ts()}] [{self._elapsed()}] {text}")

    def separator(self, label: str = ""):
        """Write a visual separator."""
        with self._lock:
            if label:
                self._write(f"\n{'~'*40} {label} {'~'*40}")
            else:
                self._write(f"\n{'~'*80}")


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_instance: Optional[DebugLogger] = None
_init_lock = threading.Lock()


def get_debug_logger() -> DebugLogger:
    """Get the global debug logger singleton."""
    global _instance
    if _instance is None:
        with _init_lock:
            if _instance is None:
                _instance = DebugLogger()
    return _instance


# Convenience alias
dbg = get_debug_logger()
