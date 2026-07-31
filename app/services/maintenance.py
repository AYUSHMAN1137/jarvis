"""Housekeeping for everything under data/: retention, archiving, backups.

Nothing in data/ was ever pruned. Measured on a real install: 41 debug log
files, 159 TTS clips (2.3 MB), a stale pre-migration .bak, and smoke-test chat
JSON -- all growing forever, with no backup of any database.

Design rules
------------
* Fail-soft, always. Every job is wrapped; a maintenance failure must never stop
  JARVIS from starting. Each returns a small dict describing what it did.
* Never destroy user history. Old action rows are moved to `actions_archive`,
  not deleted. Only re-creatable artefacts (TTS clips, debug logs, offloaded
  tool results) are actually removed.
* Backups use SQLite's ``.backup()`` API rather than copying the file. With WAL
  enabled a plain file copy can capture a torn state.

Entry point: ``run_startup_maintenance()``, called once from the lifespan.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger("J.A.R.V.I.S")

def _cfg(name: str, default: Any) -> Any:
    """Read one config value, tolerating a missing or partial config module.

    Deliberately per-name rather than ``from config import (...)``: a single
    missing name in that form leaves *every* name unbound, which silently
    disabled this whole module depending on import order.
    """
    try:
        import config as _config
        return getattr(_config, name, default)
    except Exception:  # noqa: BLE001 - module must stay importable
        return default


_BASE_DIR = Path(_cfg("BASE_DIR", Path(__file__).resolve().parent.parent.parent))

MAINTENANCE_ENABLED = bool(_cfg("MAINTENANCE_ENABLED", True))
BACKUP_ENABLED = bool(_cfg("BACKUP_ENABLED", True))
BACKUP_DIR = Path(_cfg("BACKUP_DIR", _BASE_DIR / "data" / "backups"))
BACKUP_KEEP_DAYS = int(_cfg("BACKUP_KEEP_DAYS", 7))

RETENTION_DEBUG_LOG_DAYS = int(_cfg("RETENTION_DEBUG_LOG_DAYS", 7))
RETENTION_VOICE_CACHE_FILES = int(_cfg("RETENTION_VOICE_CACHE_FILES", 5000))
RETENTION_VOICE_CACHE_MB = int(_cfg("RETENTION_VOICE_CACHE_MB", 500))
RETENTION_TOOL_RESULT_DAYS = int(_cfg("RETENTION_TOOL_RESULT_DAYS", 2))
RETENTION_ACTION_ARCHIVE_DAYS = int(_cfg("RETENTION_ACTION_ARCHIVE_DAYS", 90))

VOICE_CACHE_DIR = Path(_cfg("VOICE_CACHE_DIR", _BASE_DIR / "data" / "voice_cache"))
CHATS_DATA_DIR = Path(_cfg("CHATS_DATA_DIR", _BASE_DIR / "data" / "chats_data"))
MEMORY_DB_PATH = Path(_cfg("MEMORY_DB_PATH", _BASE_DIR / "data" / "memory.db"))
SKILLS_DB_PATH = Path(_cfg("SKILLS_DB_PATH", _BASE_DIR / "data" / "skills.db"))
COMMAND_CACHE_DB_PATH = Path(_cfg("COMMAND_CACHE_DB_PATH",
                                  _BASE_DIR / "data" / "command_cache.db"))
USER_MODEL_DB_PATH = Path(_cfg("USER_MODEL_DB_PATH", _BASE_DIR / "data" / "user_model.db"))
PROACTIVE_DB_PATH = Path(_cfg("PROACTIVE_DB_PATH", _BASE_DIR / "data" / "proactive.db"))

DEBUG_LOG_DIR = _BASE_DIR / "data" / "debug_logs"
TOOL_RESULT_DIR = _BASE_DIR / "data" / "tool_results"

# Logs that are appended to across runs, not per-session -- never age these out.
_PERSISTENT_LOGS = {"server.log", "trace.log"}


def _all_databases() -> List[Path]:
    return [MEMORY_DB_PATH, SKILLS_DB_PATH, COMMAND_CACHE_DB_PATH,
            USER_MODEL_DB_PATH, PROACTIVE_DB_PATH]


def _dir_files(folder: Path, pattern: str = "*") -> List[Path]:
    if not folder or not folder.exists():
        return []
    return [f for f in folder.glob(pattern) if f.is_file()]


def _unlink(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except OSError as exc:
        logger.debug("[MAINT] could not remove %s: %s", path.name, exc)
        return False


# --------------------------------------------------------------------------- #
# retention jobs
# --------------------------------------------------------------------------- #
def prune_debug_logs(days: int = None) -> Dict[str, Any]:
    """Delete per-session debug logs older than `days`."""
    days = RETENTION_DEBUG_LOG_DAYS if days is None else days
    if days <= 0:
        return {"removed": 0, "skipped": "disabled"}
    cutoff = time.time() - days * 86400
    removed = freed = 0
    for f in _dir_files(DEBUG_LOG_DIR):
        if f.name in _PERSISTENT_LOGS:
            continue
        try:
            stat = f.stat()
        except OSError:
            continue
        if stat.st_mtime < cutoff and _unlink(f):
            removed += 1
            freed += stat.st_size
    return {"removed": removed, "freed_bytes": freed}


def prune_voice_cache(max_files: int = None, max_mb: int = None) -> Dict[str, Any]:
    """Evict the least recently used TTS clips down to the count/size caps."""
    max_files = RETENTION_VOICE_CACHE_FILES if max_files is None else max_files
    max_mb = RETENTION_VOICE_CACHE_MB if max_mb is None else max_mb

    files = []
    for f in _dir_files(VOICE_CACHE_DIR):
        try:
            stat = f.stat()
        except OSError:
            continue
        files.append((stat.st_mtime, stat.st_size, f))
    if not files:
        return {"removed": 0, "freed_bytes": 0}

    files.sort(key=lambda row: row[0], reverse=True)  # newest first
    budget_bytes = max_mb * 1024 * 1024
    removed = freed = 0
    kept_bytes = 0
    for index, (_mtime, size, path) in enumerate(files):
        over_count = max_files > 0 and index >= max_files
        over_size = budget_bytes > 0 and kept_bytes + size > budget_bytes
        if (over_count or over_size) and _unlink(path):
            removed += 1
            freed += size
        else:
            kept_bytes += size
    return {"removed": removed, "freed_bytes": freed, "kept": len(files) - removed}


def prune_tool_results(days: int = None) -> Dict[str, Any]:
    """Delete offloaded tool-result files; they only matter within one turn."""
    days = RETENTION_TOOL_RESULT_DAYS if days is None else days
    if not TOOL_RESULT_DIR or not TOOL_RESULT_DIR.exists() or days <= 0:
        return {"removed": 0}
    cutoff = time.time() - days * 86400
    removed = freed = 0
    for f in TOOL_RESULT_DIR.rglob("*"):
        if not f.is_file():
            continue
        try:
            stat = f.stat()
        except OSError:
            continue
        if stat.st_mtime < cutoff and _unlink(f):
            removed += 1
            freed += stat.st_size
    for d in sorted(TOOL_RESULT_DIR.rglob("*"), reverse=True):
        if d.is_dir():
            try:
                d.rmdir()  # only succeeds when empty
            except OSError:
                pass
    return {"removed": removed, "freed_bytes": freed}


def cleanup_stray_files() -> Dict[str, Any]:
    """Remove leftovers that are never read again: old backups, test artefacts."""
    removed: List[str] = []
    data_dir = _BASE_DIR / "data"
    for pattern in ("*.pre-v2.bak", "*.db.bak"):
        for f in data_dir.glob(pattern):
            if _unlink(f):
                removed.append(f.name)
    if CHATS_DATA_DIR.exists():
        for f in CHATS_DATA_DIR.glob("chat_smoke*.json"):
            if _unlink(f):
                removed.append(f.name)
    return {"removed": len(removed), "names": removed}


def archive_old_actions(days: int = None, db_path: Any = None) -> Dict[str, Any]:
    """Move action rows older than `days` into actions_archive.

    Archived, never deleted -- the action log is the raw material Phase 8 learns
    habits from, and the user may want it back.
    """
    days = RETENTION_ACTION_ARCHIVE_DAYS if days is None else days
    path = Path(db_path or MEMORY_DB_PATH)
    if days <= 0 or not path.exists():
        return {"archived": 0}

    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    conn = sqlite3.connect(str(path))
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(actions)")]
        if not cols:
            return {"archived": 0}
        conn.execute("CREATE TABLE IF NOT EXISTS actions_archive "
                     "AS SELECT * FROM actions WHERE 0")
        archive_cols = [r[1] for r in conn.execute("PRAGMA table_info(actions_archive)")]
        shared = [c for c in cols if c in archive_cols]
        names = ", ".join(f'"{c}"' for c in shared)
        cur = conn.execute(
            f"INSERT INTO actions_archive ({names}) "
            f"SELECT {names} FROM actions WHERE created_at < ?", (cutoff,))
        moved = cur.rowcount or 0
        if moved:
            conn.execute("DELETE FROM actions WHERE created_at < ?", (cutoff,))
        conn.commit()
        return {"archived": moved}
    except sqlite3.Error as exc:
        logger.debug("[MAINT] action archive skipped: %s", exc)
        return {"archived": 0, "error": str(exc)}
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# backup
# --------------------------------------------------------------------------- #
def backup_databases(target_dir: Any = None, keep_days: int = None) -> Dict[str, Any]:
    """Snapshot every database into data/backups/YYYY-MM-DD/.

    Uses SQLite's online backup API: a plain file copy of a WAL database can
    capture a torn state, because the newest committed rows may still live in
    the -wal file rather than the database itself.
    """
    if not BACKUP_ENABLED:
        return {"backed_up": 0, "skipped": "disabled"}
    keep_days = BACKUP_KEEP_DAYS if keep_days is None else keep_days
    root = Path(target_dir or BACKUP_DIR)
    today = root / date.today().isoformat()

    try:
        today.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.debug("[MAINT] backup directory unavailable: %s", exc)
        return {"backed_up": 0, "error": str(exc)}

    done, failed = [], []
    for db in _all_databases():
        if not db.exists():
            continue
        destination = today / db.name
        try:
            source = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                snapshot = sqlite3.connect(str(destination))
                try:
                    source.backup(snapshot)
                finally:
                    snapshot.close()
            finally:
                source.close()
            done.append(db.name)
        except sqlite3.Error as exc:
            failed.append(db.name)
            logger.debug("[MAINT] backup failed for %s: %s", db.name, exc)

    pruned = 0
    if keep_days > 0:
        cutoff = date.today() - timedelta(days=keep_days)
        for folder in root.iterdir() if root.exists() else []:
            if not folder.is_dir():
                continue
            try:
                if date.fromisoformat(folder.name) < cutoff:
                    shutil.rmtree(folder, ignore_errors=True)
                    pruned += 1
            except ValueError:
                continue  # not a dated folder -- leave it alone
    return {"backed_up": len(done), "failed": failed, "pruned_days": pruned,
            "path": str(today)}


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def run_startup_maintenance() -> Dict[str, Any]:
    """Run every job once. Never raises."""
    if not MAINTENANCE_ENABLED:
        return {"skipped": "disabled"}

    results: Dict[str, Any] = {}
    jobs = (
        ("debug_logs", prune_debug_logs),
        ("voice_cache", prune_voice_cache),
        ("tool_results", prune_tool_results),
        ("stray_files", cleanup_stray_files),
        ("actions_archive", archive_old_actions),
        ("backup", backup_databases),
    )
    for name, job in jobs:
        try:
            results[name] = job()
        except Exception as exc:  # noqa: BLE001 - one bad job must not stop the rest
            results[name] = {"error": str(exc)}
            logger.debug("[MAINT] job %s failed: %s", name, exc)

    freed = sum(int(r.get("freed_bytes", 0) or 0) for r in results.values()
                if isinstance(r, dict))
    removed = sum(int(r.get("removed", 0) or 0) for r in results.values()
                  if isinstance(r, dict))
    backed = results.get("backup", {}).get("backed_up", 0) if isinstance(
        results.get("backup"), dict) else 0
    logger.info("[MAINT] removed %d file(s), freed %.1f KB, archived %d action(s), "
                "backed up %d database(s).",
                removed, freed / 1024.0,
                results.get("actions_archive", {}).get("archived", 0)
                if isinstance(results.get("actions_archive"), dict) else 0,
                backed)
    return results
