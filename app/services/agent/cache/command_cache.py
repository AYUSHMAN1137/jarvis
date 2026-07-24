"""Phase 6 -- Command Cache (fast local cache, verified-only).

A tiny, fail-soft SQLite store that remembers the *resolved action* for a
command JARVIS has already VERIFIED works, so the next time the exact same
command comes we can skip the brain/LLM (and even the planner) and run it
directly. This is the speed layer (speed #2) but it never sacrifices
reliability (#1):

  * Only commands the Phase 4 Checker marked PASS are ever promoted here.
  * If a cached command later fails verification, it is evicted immediately.
  * A cache *miss* (or anything unsafe) silently falls back to the normal path.
  * Nothing about any specific command is hardcoded -- every entry is learned
    at runtime from a real, verified execution.

Layers (this file = the L1/L4 exact store; L2 semantic lives in the coordinator
which reuses the existing FAISS vector store):
  * L1 exact-command   -> a single tool call OR a verified multi-step plan.
  * L4 static-response -> a canned spoken answer for a stable Q (e.g. identity).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

import config as _cfg

logger = logging.getLogger("J.A.R.V.I.S")

_DEFAULT_DB = getattr(
    _cfg, "COMMAND_CACHE_DB_PATH", _cfg.BASE_DIR / "data" / "command_cache.db"
)
_DEFAULT_MAX_ENTRIES = int(getattr(_cfg, "CACHE_MAX_ENTRIES", 500))

# Cache "kinds" -- what the stored payload represents.
KIND_TOOL = "tool"          # payload = {"tool": str, "args": dict, "say": str?}
KIND_PLAN = "plan"          # payload = {"steps": [...], "say": str?}
KIND_RESPONSE = "response"  # payload = {"say": str}
VALID_KINDS = (KIND_TOOL, KIND_PLAN, KIND_RESPONSE)


class CommandCache:
    """Local, fail-soft cache of a verified command -> its resolved action."""

    def __init__(self, db_path: Any = None, max_entries: Optional[int] = None) -> None:
        self._db_path = str(db_path or _DEFAULT_DB)
        self.max_entries = max(
            16, int(max_entries if max_entries is not None else _DEFAULT_MAX_ENTRIES)
        )
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self.enabled = False
        try:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
            except Exception:  # noqa: BLE001 - WAL is an optimization, not required
                pass
            self._init_schema()
            self.enabled = True
            logger.info("[CACHE] command cache ready (%s, max=%d).", self._db_path, self.max_entries)
        except Exception as e:  # noqa: BLE001
            logger.warning("[CACHE] init failed, cache disabled: %s", e)
            self.enabled = False

    # -- schema ---------------------------------------------------------- #
    def _init_schema(self) -> None:
        c = self._conn
        c.execute(
            """CREATE TABLE IF NOT EXISTS command_cache(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL,
                verified INTEGER DEFAULT 1,
                hits INTEGER DEFAULT 0,
                fail_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_used TEXT)"""
        )
        c.commit()

    # -- helpers --------------------------------------------------------- #
    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def normalize(text: Any) -> str:
        """Stable key: lowercase, collapse whitespace, strip edge punctuation.
        Generic -- no hardcoded command list."""
        s = str(text or "").strip().lower()
        s = " ".join(s.split())
        return s.strip(" .!?,;:\"'")

    @staticmethod
    def _dumps(payload: Any) -> str:
        try:
            return json.dumps(payload, default=str)[:8000]
        except Exception:  # noqa: BLE001
            return "{}"

    @staticmethod
    def _loads(s: Any) -> Any:
        try:
            return json.loads(s)
        except Exception:  # noqa: BLE001
            return {}

    # -- writes ---------------------------------------------------------- #
    def put(self, trigger: Any, kind: str, payload: Dict[str, Any], verified: bool = True) -> bool:
        """Upsert a verified command -> action mapping. Returns True on success."""
        if not self.enabled or not self._conn:
            return False
        if kind not in VALID_KINDS:
            logger.debug("[CACHE] put rejected: bad kind %r", kind)
            return False
        try:
            trig = self.normalize(trigger)
            if not trig or not isinstance(payload, dict):
                return False
            blob = self._dumps(payload)
            now = self._now()
            with self._lock:
                c = self._conn
                row = c.execute(
                    "SELECT id FROM command_cache WHERE trigger=?", (trig,)
                ).fetchone()
                if row:
                    c.execute(
                        "UPDATE command_cache SET kind=?, payload=?, verified=?, "
                        "status='active', fail_count=0, updated_at=? WHERE id=?",
                        (kind, blob, 1 if verified else 0, now, row[0]),
                    )
                else:
                    c.execute(
                        "INSERT INTO command_cache(trigger, kind, payload, verified, "
                        "hits, fail_count, status, created_at, updated_at, last_used) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (trig, kind, blob, 1 if verified else 0, 0, 0, "active", now, now, None),
                    )
                c.commit()
                self._enforce_cap_locked()
            logger.info("[CACHE] stored %s entry: '%s'", kind, trig)
            return True
        except Exception as e:  # noqa: BLE001
            logger.debug("[CACHE] put failed: %s", e)
            return False

    def _enforce_cap_locked(self) -> None:
        """Evict least-recently-used active rows above the cap. Caller holds lock."""
        try:
            c = self._conn
            n = c.execute(
                "SELECT COUNT(*) FROM command_cache WHERE status='active'"
            ).fetchone()[0]
            if n <= self.max_entries:
                return
            over = n - self.max_entries
            # LRU: never-used (NULL last_used) first, then oldest, then fewest hits.
            rows = c.execute(
                "SELECT id FROM command_cache WHERE status='active' "
                "ORDER BY (last_used IS NULL) DESC, last_used ASC, hits ASC LIMIT ?",
                (int(over),),
            ).fetchall()
            for (rid,) in rows:
                c.execute("UPDATE command_cache SET status='evicted' WHERE id=?", (rid,))
            c.commit()
        except Exception as e:  # noqa: BLE001
            logger.debug("[CACHE] cap enforce failed: %s", e)

    def record_hit(self, trigger: Any) -> None:
        if not self.enabled or not self._conn:
            return
        try:
            trig = self.normalize(trigger)
            now = self._now()
            with self._lock:
                self._conn.execute(
                    "UPDATE command_cache SET hits=hits+1, last_used=? WHERE trigger=?",
                    (now, trig),
                )
                self._conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.debug("[CACHE] record_hit failed: %s", e)

    def evict(self, trigger: Any) -> None:
        """Remove a command from the fast path (e.g. it later failed to verify)."""
        if not self.enabled or not self._conn:
            return
        try:
            trig = self.normalize(trigger)
            now = self._now()
            with self._lock:
                self._conn.execute(
                    "UPDATE command_cache SET status='evicted', fail_count=fail_count+1, "
                    "updated_at=? WHERE trigger=?",
                    (now, trig),
                )
                self._conn.commit()
            logger.info("[CACHE] evicted: '%s'", trig)
        except Exception as e:  # noqa: BLE001
            logger.debug("[CACHE] evict failed: %s", e)

    # -- reads ----------------------------------------------------------- #
    def get(self, trigger: Any) -> Optional[Dict[str, Any]]:
        """Return an active, verified cache entry for the exact command, or None."""
        if not self.enabled or not self._conn:
            return None
        try:
            trig = self.normalize(trigger)
            if not trig:
                return None
            with self._lock:
                row = self._conn.execute(
                    "SELECT trigger, kind, payload, verified, hits, status FROM command_cache "
                    "WHERE trigger=? AND status='active' AND verified=1 LIMIT 1",
                    (trig,),
                ).fetchone()
            if not row:
                return None
            return {
                "trigger": row[0], "kind": row[1], "payload": self._loads(row[2]),
                "verified": bool(row[3]), "hits": row[4], "status": row[5],
            }
        except Exception as e:  # noqa: BLE001
            logger.debug("[CACHE] get failed: %s", e)
            return None

    def list_entries(self, limit: int = 50) -> List[Dict[str, Any]]:
        if not self.enabled or not self._conn:
            return []
        try:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT trigger, kind, hits, fail_count, status, last_used FROM command_cache "
                    "WHERE status='active' ORDER BY hits DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
            return [
                {"trigger": r[0], "kind": r[1], "hits": r[2], "fail_count": r[3],
                 "status": r[4], "last_used": r[5]}
                for r in rows
            ]
        except Exception as e:  # noqa: BLE001
            logger.debug("[CACHE] list_entries failed: %s", e)
            return []

    def stats(self) -> dict:
        if not self.enabled or not self._conn:
            return {"enabled": False, "active": 0, "total": 0, "hits": 0}
        try:
            with self._lock:
                active = self._conn.execute(
                    "SELECT COUNT(*) FROM command_cache WHERE status='active'"
                ).fetchone()[0]
                total = self._conn.execute(
                    "SELECT COUNT(*) FROM command_cache"
                ).fetchone()[0]
                hits = self._conn.execute(
                    "SELECT COALESCE(SUM(hits),0) FROM command_cache"
                ).fetchone()[0]
            return {"enabled": True, "active": active, "total": total, "hits": int(hits)}
        except Exception:  # noqa: BLE001
            return {"enabled": False, "active": 0, "total": 0, "hits": 0}
