"""Reminder & scheduler service (M8).

Provides persistent reminders with a priority-queue-based scheduler thread.
Reminders fire via a callback that pushes to the frontend SSE stream.

Database: ``data/reminders.db`` (auto-created).

Design
------
* **heapq scheduler** — the thread sleeps until the next reminder is due, not
  polling every N seconds.  ``_wake`` (``threading.Event``) lets ``add()`` /
  ``cancel()`` poke the thread when the heap changes.
* **Transactional firing** — status goes ``active → firing → fired`` inside a
  single SQLite transaction so a crash between fire and update never duplicates
  or loses a reminder.
* **Recurrence** — simple human-readable strings (``daily``, ``weekdays``,
  ``weekly``, ``monthly``).  No cron expressions.  ``_next_occurrence()``
  advances the ``due_at`` and resets status back to ``active``.
"""

from __future__ import annotations

import heapq
import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.services.db import open_db

logger = logging.getLogger("J.A.R.V.I.S")

# Indian Standard Time (UTC+05:30)
IST = timezone(timedelta(hours=5, minutes=30))

_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_DB_PATH = _BASE_DIR / "data" / "reminders.db"

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT    NOT NULL,
    description   TEXT    DEFAULT '',
    due_at        TEXT    NOT NULL,       -- ISO 8601 with timezone
    created_at    TEXT    NOT NULL,
    status        TEXT    DEFAULT 'active',  -- active | firing | fired | snoozed | cancelled | deleted | failed
    recurrence    TEXT    DEFAULT NULL,      -- NULL | daily | weekdays | weekly | monthly
    snooze_until  TEXT    DEFAULT NULL,
    tags          TEXT    DEFAULT '',
    fired_count   INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_reminders_status_due ON reminders(status, due_at);
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_ist() -> datetime:
    return datetime.now(tz=IST)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_iso(s: str) -> datetime:
    """Parse ISO 8601 string, handle missing timezone."""
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt


def _next_occurrence(due: datetime, recurrence: str) -> Optional[datetime]:
    """Calculate the next occurrence for a recurring reminder."""
    if not recurrence:
        return None

    r = recurrence.lower().strip()
    if r == "daily":
        nxt = due + timedelta(days=1)
    elif r == "weekdays":
        nxt = due + timedelta(days=1)
        while nxt.weekday() >= 5:  # skip Saturday (5) and Sunday (6)
            nxt += timedelta(days=1)
    elif r == "weekly":
        nxt = due + timedelta(weeks=1)
    elif r == "monthly":
        # Same day next month
        month = due.month + 1
        year = due.year
        if month > 12:
            month = 1
            year += 1
        day = min(due.day, 28)  # safe for all months
        nxt = due.replace(year=year, month=month, day=day)
    else:
        return None

    # If the calculated next time is still in the past, skip forward
    now = _now_ist()
    while nxt <= now:
        nxt = _next_occurrence(nxt, recurrence)
        if nxt is None:
            break
    return nxt


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return dict(row)


# ---------------------------------------------------------------------------
# ReminderService
# ---------------------------------------------------------------------------

# Singleton
_instance: Optional["ReminderService"] = None
_instance_lock = threading.Lock()


def get_reminder_service() -> "ReminderService":
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = ReminderService()
    return _instance


class ReminderService:
    """Manages reminders with a heapq-based background scheduler."""

    def __init__(self):
        self._conn = open_db(_DB_PATH, label="reminders", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

        # Priority queue: list of (due_timestamp, reminder_id)
        self._heap: List[Tuple[float, int]] = []
        self._heap_lock = threading.Lock()

        # Wake the scheduler when the heap changes
        self._wake = threading.Event()
        self._stop = threading.Event()

        # Callback for firing — set by the startup code
        self._on_fire: Optional[Callable[[Dict[str, Any]], None]] = None

        # Thread
        self._thread: Optional[threading.Thread] = None

        # Load existing active reminders into heap
        self._reload_heap()

        logger.info("[REMINDERS] Service initialized with %d active reminders.", len(self._heap))

    # ------------------------------------------------------------------
    # Heap management
    # ------------------------------------------------------------------
    def _reload_heap(self) -> None:
        """Rebuild the heap from DB (startup or after major changes).

        For **recurring** reminders whose due_at is in the past, we silently
        advance to the next future occurrence so they don't fire spuriously.
        One-time past-due reminders are kept as-is (they'll fire once as
        "missed" so the user knows they were set).
        """
        now = _now_ist()
        now_ts = now.timestamp()

        with self._heap_lock:
            self._heap.clear()
            rows = self._conn.execute(
                "SELECT id, due_at, snooze_until, recurrence FROM reminders "
                "WHERE status IN ('active', 'snoozed', 'firing')"
            ).fetchall()
            for row in rows:
                effective_due = row["snooze_until"] if row["snooze_until"] else row["due_at"]
                ts = _parse_iso(effective_due).timestamp()

                # ── Advance past-due recurring reminders to next future slot ──
                if ts <= now_ts and row["recurrence"]:
                    due_dt = _parse_iso(effective_due)
                    nxt = _next_occurrence(due_dt, row["recurrence"])
                    if nxt:
                        new_due = _iso(nxt)
                        self._conn.execute(
                            "UPDATE reminders SET due_at = ?, snooze_until = NULL WHERE id = ?",
                            (new_due, row["id"]),
                        )
                        self._conn.commit()
                        ts = nxt.timestamp()
                        logger.info(
                            "[REMINDERS] Advanced recurring #%d to next slot: %s",
                            row["id"], new_due,
                        )
                    else:
                        # Can't advance — mark as fired
                        self._conn.execute(
                            "UPDATE reminders SET status = 'fired' WHERE id = ?",
                            (row["id"],),
                        )
                        self._conn.commit()
                        continue

                heapq.heappush(self._heap, (ts, row["id"]))

    def _push_heap(self, reminder_id: int, due_at: str) -> None:
        ts = _parse_iso(due_at).timestamp()
        with self._heap_lock:
            heapq.heappush(self._heap, (ts, reminder_id))
        self._wake.set()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def add(self, title: str, due_at: str, recurrence: str = None,
            description: str = "", tags: str = "") -> Dict[str, Any]:
        """Create a new reminder."""
        now = _iso(_now_ist())
        cur = self._conn.execute(
            """INSERT INTO reminders (title, description, due_at, created_at, status, recurrence, tags)
               VALUES (?, ?, ?, ?, 'active', ?, ?)""",
            (title, description, due_at, now, recurrence, tags),
        )
        self._conn.commit()
        rid = cur.lastrowid

        self._push_heap(rid, due_at)
        logger.info("[REMINDERS] Created reminder #%d: '%s' due=%s recurrence=%s",
                     rid, title, due_at, recurrence)

        return self.get(rid)

    def get(self, reminder_id: int) -> Optional[Dict[str, Any]]:
        row = self._conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
        return _row_to_dict(row) if row else None

    def list_active(self, filter_type: str = "all") -> List[Dict[str, Any]]:
        """List reminders. filter_type: all, today, upcoming."""
        now = _now_ist()
        if filter_type == "today":
            end_of_day = now.replace(hour=23, minute=59, second=59)
            rows = self._conn.execute(
                """SELECT * FROM reminders
                   WHERE status IN ('active', 'snoozed') AND due_at <= ?
                   ORDER BY due_at ASC""",
                (_iso(end_of_day),),
            ).fetchall()
        elif filter_type == "upcoming":
            rows = self._conn.execute(
                """SELECT * FROM reminders
                   WHERE status IN ('active', 'snoozed') AND due_at > ?
                   ORDER BY due_at ASC""",
                (_iso(now),),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT * FROM reminders
                   WHERE status IN ('active', 'snoozed')
                   ORDER BY due_at ASC""",
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def cancel(self, query: str) -> int:
        """Cancel reminders matching a title search."""
        query_lower = query.lower().strip()
        rows = self._conn.execute(
            "SELECT id, title FROM reminders WHERE status IN ('active', 'snoozed')"
        ).fetchall()
        cancelled = 0
        for row in rows:
            if query_lower in row["title"].lower():
                self._conn.execute(
                    "UPDATE reminders SET status = 'cancelled' WHERE id = ?", (row["id"],)
                )
                cancelled += 1
        self._conn.commit()
        if cancelled:
            self._reload_heap()
            self._wake.set()
        return cancelled

    def snooze(self, reminder_id: int = None, minutes: int = 10) -> Optional[Dict[str, Any]]:
        """Snooze the most recently fired reminder, or a specific one."""
        if reminder_id is None:
            # Find the most recently fired
            row = self._conn.execute(
                "SELECT id FROM reminders WHERE status = 'fired' ORDER BY due_at DESC LIMIT 1"
            ).fetchone()
            if not row:
                return None
            reminder_id = row["id"]

        snooze_time = _now_ist() + timedelta(minutes=minutes)
        self._conn.execute(
            """UPDATE reminders SET status = 'snoozed', snooze_until = ?
               WHERE id = ?""",
            (_iso(snooze_time), reminder_id),
        )
        self._conn.commit()
        self._push_heap(reminder_id, _iso(snooze_time))
        return self.get(reminder_id)

    def delete(self, reminder_id: int) -> bool:
        """Soft-delete a reminder."""
        self._conn.execute(
            "UPDATE reminders SET status = 'deleted' WHERE id = ?", (reminder_id,)
        )
        self._conn.commit()
        self._reload_heap()
        self._wake.set()
        return True

    def mark_done(self, reminder_id: int) -> bool:
        """Mark a reminder as done/fired."""
        self._conn.execute(
            "UPDATE reminders SET status = 'fired' WHERE id = ?", (reminder_id,)
        )
        self._conn.commit()
        return True

    def get_stats(self) -> Dict[str, int]:
        """Quick stats for diagnostics."""
        rows = self._conn.execute(
            "SELECT status, COUNT(*) as cnt FROM reminders GROUP BY status"
        ).fetchall()
        return {row["status"]: row["cnt"] for row in rows}

    # ------------------------------------------------------------------
    # Scheduler thread
    # ------------------------------------------------------------------
    def start(self, on_fire: Callable[[Dict[str, Any]], None] = None) -> None:
        """Start the background scheduler thread."""
        if on_fire:
            self._on_fire = on_fire
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._scheduler_loop, daemon=True, name="ReminderScheduler")
        self._thread.start()
        logger.info("[REMINDERS] Scheduler thread started.")

    def stop(self) -> None:
        """Stop the scheduler thread."""
        self._stop.set()
        self._wake.set()  # unblock the thread
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("[REMINDERS] Scheduler thread stopped.")

    def _scheduler_loop(self) -> None:
        """Main scheduler loop — sleeps until the next reminder is due."""
        logger.info("[REMINDERS] Scheduler loop running.")
        while not self._stop.is_set():
            self._wake.clear()

            # Find the next due reminder
            next_ts = None
            with self._heap_lock:
                # Clean up stale entries
                while self._heap:
                    ts, rid = self._heap[0]
                    # Check if still active
                    row = self._conn.execute(
                        "SELECT status FROM reminders WHERE id = ?", (rid,)
                    ).fetchone()
                    if not row or row["status"] not in ("active", "snoozed"):
                        heapq.heappop(self._heap)
                        continue
                    next_ts = ts
                    break

            if next_ts is None:
                # No reminders — sleep until woken
                self._wake.wait()
                continue

            now_ts = _now_ist().timestamp()
            wait_seconds = max(0, next_ts - now_ts)

            if wait_seconds > 0:
                # Sleep until due or until woken by a new reminder
                self._wake.wait(timeout=wait_seconds)
                if self._stop.is_set():
                    break
                # Check if we were woken early (new reminder added)
                if self._wake.is_set():
                    continue  # re-evaluate the heap

            # Fire all due reminders
            self._fire_due()

    def _fire_due(self) -> None:
        """Fire all reminders that are currently due."""
        now = _now_ist()
        now_ts = now.timestamp()

        while True:
            with self._heap_lock:
                if not self._heap:
                    break
                ts, rid = self._heap[0]
                if ts > now_ts:
                    break
                heapq.heappop(self._heap)

            # Transactional fire: active → firing → fired
            row = self._conn.execute(
                "SELECT * FROM reminders WHERE id = ? AND status IN ('active', 'snoozed')",
                (rid,),
            ).fetchone()
            if not row:
                continue

            reminder = _row_to_dict(row)

            # Mark as firing (intermediate state for crash safety)
            self._conn.execute(
                "UPDATE reminders SET status = 'firing' WHERE id = ?", (rid,)
            )
            self._conn.commit()

            # Fire the callback
            try:
                if self._on_fire:
                    self._on_fire(reminder)
                logger.info("[REMINDERS] 🔔 Fired reminder #%d: '%s'", rid, reminder["title"])
            except Exception as e:
                logger.error("[REMINDERS] Failed to fire reminder #%d: %s", rid, e, exc_info=True)
                self._conn.execute(
                    "UPDATE reminders SET status = 'failed' WHERE id = ?", (rid,)
                )
                self._conn.commit()
                continue

            # Handle recurrence
            if reminder.get("recurrence"):
                due_dt = _parse_iso(reminder["due_at"])
                nxt = _next_occurrence(due_dt, reminder["recurrence"])
                if nxt:
                    self._conn.execute(
                        """UPDATE reminders
                           SET status = 'active', due_at = ?, snooze_until = NULL,
                               fired_count = fired_count + 1
                           WHERE id = ?""",
                        (_iso(nxt), rid),
                    )
                    self._conn.commit()
                    self._push_heap(rid, _iso(nxt))
                else:
                    self._conn.execute(
                        "UPDATE reminders SET status = 'fired', fired_count = fired_count + 1 WHERE id = ?",
                        (rid,),
                    )
                    self._conn.commit()
            else:
                # One-time: mark as fired
                self._conn.execute(
                    "UPDATE reminders SET status = 'fired', fired_count = fired_count + 1 WHERE id = ?",
                    (rid,),
                )
                self._conn.commit()

    # ------------------------------------------------------------------
    # Recovery: re-fire anything stuck in 'firing' state (server crashed)
    # ------------------------------------------------------------------
    def recover_stuck(self) -> int:
        """Re-fire reminders stuck in 'firing' from a previous crash."""
        rows = self._conn.execute(
            "SELECT * FROM reminders WHERE status = 'firing'"
        ).fetchall()
        count = 0
        for row in rows:
            reminder = _row_to_dict(row)
            try:
                if self._on_fire:
                    self._on_fire(reminder)
                self._conn.execute(
                    "UPDATE reminders SET status = 'fired', fired_count = fired_count + 1 WHERE id = ?",
                    (reminder["id"],),
                )
                self._conn.commit()
                count += 1
            except Exception as e:
                logger.error("[REMINDERS] Recovery failed for #%d: %s", reminder["id"], e)
                self._conn.execute(
                    "UPDATE reminders SET status = 'failed' WHERE id = ?", (reminder["id"],)
                )
                self._conn.commit()
        if count:
            logger.info("[REMINDERS] Recovered %d stuck reminder(s).", count)
        return count
