"""One place where every SQLite database in JARVIS is opened and closed.

Why this exists
---------------
Each subsystem used to call ``sqlite3.connect`` itself. Three of them set
``journal_mode=WAL``, three did not, none set an autocheckpoint, and *nothing*
closed a connection on shutdown. The result, measured on a real install:

    memory.db  = 4 KB      <- the actual database
    memory.db-wal = 2 MB   <- everything ever written, still in the log

SQLite only merges the write-ahead log back into the database when the last
connection closes cleanly, or when an autocheckpoint threshold is crossed.
Neither was happening, so the log grew forever and every read had to scan it.

Two connections also pointed at ``memory.db`` (``memory_service`` and
``context_engine``). A checkpoint cannot truncate the log while another
connection is still open, so even a clean close of one of them achieved
nothing. Tracking every connection in one registry is what makes the shutdown
checkpoint actually work.

Design rules
------------
* Fail-soft. A PRAGMA that does not apply, or a database that cannot be
  checkpointed, must never stop JARVIS from starting or shutting down.
* No behaviour change beyond lifecycle. ``synchronous`` is deliberately left at
  the SQLite default -- this module fixes log growth, not durability semantics.
* Callers keep owning their connection. This module only remembers it so it can
  be checkpointed and closed at the end.

Usage
-----
    from app.services.db import open_db

    self._conn = open_db(MEMORY_DB_PATH, label="memory")

and once, during shutdown::

    from app.services.db import checkpoint_and_close_all
    checkpoint_and_close_all()
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("J.A.R.V.I.S")

try:  # config is optional so this module stays importable in isolation (tests)
    from config import DB_BUSY_TIMEOUT_MS, DB_WAL_AUTOCHECKPOINT_PAGES
except Exception:  # noqa: BLE001
    DB_WAL_AUTOCHECKPOINT_PAGES = 1000
    DB_BUSY_TIMEOUT_MS = 5000

# (connection, label, path) for every database we opened, in open order.
_registry: List[Tuple[sqlite3.Connection, str, str]] = []
_registry_lock = threading.Lock()


def _is_memory_db(path: str) -> bool:
    return path == ":memory:" or path.startswith("file::memory:")


def _apply_pragmas(conn: sqlite3.Connection, path: str, label: str) -> None:
    """Apply lifecycle PRAGMAs. Each one is independently optional."""
    # busy_timeout matters for every database: JARVIS writes from the request
    # thread, the watcher thread, and the Phase 4 checker thread at once.
    for pragma, value in (("busy_timeout", DB_BUSY_TIMEOUT_MS),):
        try:
            conn.execute(f"PRAGMA {pragma}={int(value)}")
        except Exception as exc:  # noqa: BLE001
            logger.debug("[DB] %s: PRAGMA %s failed: %s", label, pragma, exc)

    if _is_memory_db(path):
        return  # an in-memory database has no WAL to manage

    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception as exc:  # noqa: BLE001 - WAL is an optimization, not required
        logger.debug("[DB] %s: WAL unavailable: %s", label, exc)
        return

    try:
        conn.execute(f"PRAGMA wal_autocheckpoint={int(DB_WAL_AUTOCHECKPOINT_PAGES)}")
    except Exception as exc:  # noqa: BLE001
        logger.debug("[DB] %s: autocheckpoint not set: %s", label, exc)

    # Clean up after the *previous* run. JARVIS is usually stopped by a hard
    # kill (scripts/stop_server.py, closing the console, start.bat freeing the
    # port), so the shutdown hook cannot be relied on -- checkpointing here is
    # the one path that always executes. Harmless when the log is already empty,
    # and simply reports busy if another connection to the same file is live.
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception as exc:  # noqa: BLE001
        logger.debug("[DB] %s: startup checkpoint skipped: %s", label, exc)


def open_db(db_path: Any, label: str = "", check_same_thread: bool = False) -> sqlite3.Connection:
    """Open a database with JARVIS' standard lifecycle settings and remember it.

    Never raises for anything other than a genuinely unopenable database -- the
    caller's existing try/except around construction stays meaningful.
    """
    path = str(db_path)
    label = label or os.path.basename(path) or path

    if not _is_memory_db(path):
        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[DB] %s: could not create parent directory: %s", label, exc)

    conn = sqlite3.connect(path, check_same_thread=check_same_thread)
    _apply_pragmas(conn, path, label)

    with _registry_lock:
        _registry.append((conn, label, path))
    return conn


def register(conn: sqlite3.Connection, label: str = "", db_path: Any = "") -> None:
    """Track a connection that was opened elsewhere (e.g. a legacy call site)."""
    if conn is None:
        return
    with _registry_lock:
        if any(existing is conn for existing, _, _ in _registry):
            return
        _registry.append((conn, label or "unknown", str(db_path)))


def unregister(conn: sqlite3.Connection) -> None:
    """Forget a connection the caller is closing itself."""
    if conn is None:
        return
    with _registry_lock:
        for i, (existing, _, _) in enumerate(_registry):
            if existing is conn:
                _registry.pop(i)
                return


def checkpoint_all() -> Dict[str, int]:
    """Merge every WAL back into its database without closing anything.

    Safe to call periodically. Uses PASSIVE so it never blocks a live reader.
    """
    return _checkpoint(mode="PASSIVE", close=False)


def checkpoint_and_close_all() -> Dict[str, int]:
    """Shutdown path: TRUNCATE every WAL, then close every connection.

    TRUNCATE (rather than PASSIVE) is what actually removes the -wal file, which
    is the difference between a 2 MB leftover log and none at all.
    """
    return _checkpoint(mode="TRUNCATE", close=True)


def _checkpoint(mode: str, close: bool) -> Dict[str, int]:
    with _registry_lock:
        entries = list(_registry)
        if close:
            _registry.clear()

    summary = {"checkpointed": 0, "closed": 0, "failed": 0}
    for conn, label, path in entries:
        if not _is_memory_db(path):
            try:
                conn.execute(f"PRAGMA wal_checkpoint({mode})")
                summary["checkpointed"] += 1
            except Exception as exc:  # noqa: BLE001
                summary["failed"] += 1
                logger.debug("[DB] %s: checkpoint failed: %s", label, exc)
        if close:
            try:
                conn.close()
                summary["closed"] += 1
            except Exception as exc:  # noqa: BLE001
                summary["failed"] += 1
                logger.debug("[DB] %s: close failed: %s", label, exc)

    if close:
        logger.info("[DB] Checkpointed %d and closed %d database connection(s)%s.",
                    summary["checkpointed"], summary["closed"],
                    f", {summary['failed']} problem(s)" if summary["failed"] else "")
    return summary


def tracked() -> List[Tuple[str, str]]:
    """(label, path) for every connection currently tracked. For diagnostics."""
    with _registry_lock:
        return [(label, path) for _, label, path in _registry]


def wal_sizes() -> Dict[str, int]:
    """Current -wal size in bytes per tracked database. For diagnostics/tests."""
    out: Dict[str, int] = {}
    for label, path in tracked():
        if _is_memory_db(path):
            continue
        try:
            out[label] = os.path.getsize(path + "-wal")
        except OSError:
            out[label] = 0
    return out


_reset_lock = threading.Lock()


def _reset_for_tests() -> None:
    """Drop the registry without touching the connections. Tests only."""
    with _reset_lock, _registry_lock:
        _registry.clear()
