"""A small searchable index of the user's own folders.

Why
---
`list_directory` only helps when you already know which folder to look in, so
"wo resume wali PDF kahan hai?" had no answer at all. This indexes the handful
of places a person actually keeps things and makes them searchable by name.

Scope is deliberately narrow: the user profile folders (Desktop, Documents,
Downloads, Pictures, Videos, Music), not the whole disk. Indexing C:\\ would be
slow, mostly noise, and would surface files the user never thinks of as theirs.

No second poller
----------------
The project already runs one background daemon that diffs system state, and
adding another was explicitly ruled out. Instead the index is built once in the
background at startup and refreshed lazily: a search that finds the index stale
kicks off one debounced rebuild and answers from what it has. Search never
blocks on indexing.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("J.A.R.V.I.S")


def _cfg(name: str, default: Any) -> Any:
    try:
        import config as _config
        return getattr(_config, name, default)
    except Exception:  # noqa: BLE001
        return default


_BASE_DIR = Path(_cfg("BASE_DIR", Path(__file__).resolve().parent.parent.parent.parent))
DB_PATH = Path(_cfg("FILE_INDEX_DB_PATH", _BASE_DIR / "data" / "file_index.db"))

USER_FOLDERS = ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music")

# Noise that would crowd out real results.
SKIP_DIRS = {
    "node_modules", "__pycache__", ".git", ".venv", "venv", "env",
    "site-packages", ".idea", ".vscode", "AppData", "$RECYCLE.BIN",
    "System Volume Information", ".cache", "dist", "build", ".next",
}
SKIP_EXTS = {".pyc", ".pyo", ".tmp", ".log", ".lock", ".part", ".crdownload"}


class FileIndex:
    def __init__(self, db_path: Any = None, roots: Optional[List[str]] = None,
                 max_files: int = 200_000, refresh_seconds: int = 900,
                 max_per_dir: int = 300) -> None:
        self.db_path = Path(db_path or DB_PATH)
        self._roots = roots
        self.max_files = int(max_files)
        # Cap per directory as well as overall. Measured on a real machine, one
        # downloaded ML dataset contributed 176,160 of 199,474 indexed files --
        # 88% of the budget, and it pushed real documents out entirely. Nobody
        # searches for "image_4032.jpg" by name; a folder that large is a data
        # dump, not somewhere you look for a file.
        self.max_per_dir = int(max_per_dir)
        self.refresh_seconds = int(refresh_seconds)
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()
        self._building = threading.Event()
        self.enabled = False
        try:
            from app.services.db import open_db
            self._conn = open_db(self.db_path, label="file_index")
            self._init_schema()
            self.enabled = True
        except Exception as exc:  # noqa: BLE001 - the tool degrades, chat does not
            logger.warning("[FILE-INDEX] unavailable: %s", exc)

    # -- schema ---------------------------------------------------------- #
    def _init_schema(self) -> None:
        c = self._conn
        c.execute("""CREATE TABLE IF NOT EXISTS files(
            path TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            name_lower TEXT NOT NULL,
            ext TEXT,
            size INTEGER,
            mtime REAL,
            folder TEXT)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_files_name ON files(name_lower)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_files_ext ON files(ext)")
        c.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
        c.commit()

    def _meta(self, key: str, default: str = "") -> str:
        try:
            row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row[0] if row else default
        except sqlite3.Error:
            return default

    def _set_meta(self, key: str, value: str) -> None:
        try:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
        except sqlite3.Error:
            pass

    # -- roots ----------------------------------------------------------- #
    def roots(self) -> List[str]:
        if self._roots is not None:
            return list(self._roots)
        home = os.path.expanduser("~")
        found = [os.path.join(home, name) for name in USER_FOLDERS]
        return [p for p in found if os.path.isdir(p)]

    # -- build ----------------------------------------------------------- #
    def age_seconds(self) -> float:
        try:
            return time.time() - float(self._meta("built_at", "0") or 0)
        except (TypeError, ValueError):
            return float("inf")

    def is_stale(self) -> bool:
        return self.age_seconds() > self.refresh_seconds

    def count(self) -> int:
        try:
            return self._conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        except sqlite3.Error:
            return 0

    def build(self) -> Dict[str, Any]:
        """Walk the roots and rewrite the index. Safe to call repeatedly."""
        if not self.enabled:
            return {"indexed": 0, "skipped": "disabled"}
        if self._building.is_set():
            return {"indexed": 0, "skipped": "already running"}
        self._building.set()
        started = time.time()
        rows: List[tuple] = []
        crowded = 0
        try:
            for root in self.roots():
                for dirpath, dirnames, filenames in os.walk(root):
                    # Prune in place so os.walk does not descend into them.
                    dirnames[:] = [d for d in dirnames
                                   if d not in SKIP_DIRS and not d.startswith(".")]
                    if self.max_per_dir and len(filenames) > self.max_per_dir:
                        crowded += 1
                        filenames = filenames[:self.max_per_dir]
                    for filename in filenames:
                        ext = os.path.splitext(filename)[1].lower()
                        if ext in SKIP_EXTS:
                            continue
                        full = os.path.join(dirpath, filename)
                        try:
                            stat = os.stat(full)
                        except OSError:
                            continue
                        rows.append((full, filename, filename.lower(), ext,
                                     stat.st_size, stat.st_mtime, dirpath))
                        if len(rows) >= self.max_files:
                            break
                    if len(rows) >= self.max_files:
                        break

            with self._lock:
                c = self._conn
                c.execute("DELETE FROM files")
                c.executemany(
                    "INSERT OR REPLACE INTO files"
                    "(path, name, name_lower, ext, size, mtime, folder) "
                    "VALUES(?,?,?,?,?,?,?)", rows)
                self._set_meta("built_at", str(time.time()))
                c.commit()
            elapsed = time.time() - started
            # Say what was left out rather than silently returning partial data.
            note = f", {crowded} crowded folder(s) truncated" if crowded else ""
            if len(rows) >= self.max_files:
                note += f", stopped at the {self.max_files:,} file cap"
            logger.info("[FILE-INDEX] indexed %d file(s) in %.1fs%s",
                        len(rows), elapsed, note)
            return {"indexed": len(rows), "seconds": round(elapsed, 1),
                    "crowded_dirs": crowded,
                    "hit_cap": len(rows) >= self.max_files}
        except Exception as exc:  # noqa: BLE001
            logger.warning("[FILE-INDEX] build failed: %s", exc)
            return {"indexed": 0, "error": str(exc)}
        finally:
            self._building.clear()

    def build_async(self) -> None:
        """Rebuild in the background. Never blocks the caller."""
        if not self.enabled or self._building.is_set():
            return
        threading.Thread(target=self.build, name="file-index-build",
                         daemon=True).start()

    # -- search ---------------------------------------------------------- #
    def search(self, query: str, limit: int = 20,
               extension: str = "") -> List[Dict[str, Any]]:
        """Rank matches: exact name, then prefix, then substring, newest first."""
        if not self.enabled:
            return []
        q = (query or "").strip().lower()
        if not q:
            return []

        # Answer from what we have and refresh behind the scenes.
        if self.is_stale():
            self.build_async()

        clauses = ["name_lower LIKE ?"]
        params: List[Any] = [f"%{q}%"]
        ext = (extension or "").strip().lower()
        if ext:
            if not ext.startswith("."):
                ext = "." + ext
            clauses.append("ext = ?")
            params.append(ext)

        sql = (
            "SELECT path, name, ext, size, mtime, folder FROM files "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY CASE "
            "  WHEN name_lower = ? THEN 0 "
            "  WHEN name_lower LIKE ? THEN 1 "
            "  ELSE 2 END, mtime DESC LIMIT ?"
        )
        params.extend([q, f"{q}%", int(limit)])
        try:
            with self._lock:
                rows = self._conn.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            logger.debug("[FILE-INDEX] search failed: %s", exc)
            return []
        return [{"path": r[0], "name": r[1], "ext": r[2], "size": r[3],
                 "mtime": r[4], "folder": r[5]} for r in rows]

    def stats(self) -> Dict[str, Any]:
        return {"enabled": self.enabled, "files": self.count(),
                "age_seconds": round(self.age_seconds(), 1),
                "building": self._building.is_set(), "roots": self.roots()}


_singleton: Optional[FileIndex] = None
_singleton_lock = threading.Lock()


def get_file_index() -> FileIndex:
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = FileIndex()
    return _singleton
