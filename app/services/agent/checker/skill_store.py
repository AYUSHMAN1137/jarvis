"""Skill store (Phase 4, Chunk 4) -- persistent, verified, reusable skills.

A "skill" is a command that JARVIS has *verified* works, so next time the same
request comes it can be trusted / fast-pathed (bridge to Phase 6 cache).

Key design points (locked + 19 Jun refinements):
  * SQLite, fail-soft. A storage problem must never crash a chat/action.
  * "N baar repeat" fluke-guard: a verified success is first logged as an
    OBSERVATION; only after the SAME (trigger, steps) succeeds >= N times is it
    crystallized into the `skills` table. One lucky success never becomes a
    skill.
  * Lifecycle: skills carry a `status` (active/stale). Repeated post-
    crystallization failures mark a skill `stale` so a stale skill self-retires
    (learned from future-JARVIS).
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from datetime import datetime
from typing import Any, List, Optional

import config as _cfg

logger = logging.getLogger("J.A.R.V.I.S")

_DEFAULT_DB = getattr(
    _cfg, "SKILLS_DB_PATH", _cfg.BASE_DIR / "data" / "skills.db"
)
_DEFAULT_MIN_REPEATS = int(getattr(_cfg, "SKILL_MIN_REPEATS", 3))


class SkillStore:
    """Local, fail-soft store of verified skills + their success observations."""

    def __init__(self, db_path: Any = None, min_repeats: Optional[int] = None) -> None:
        self._db_path = str(db_path or _DEFAULT_DB)
        self.min_repeats = max(1, int(min_repeats if min_repeats is not None else _DEFAULT_MIN_REPEATS))
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self.enabled = False
        try:
            from app.services.db import open_db
            self._conn = open_db(self._db_path, label="skills")
            self._init_schema()
            self.enabled = True
            logger.info("[SKILL] store ready (%s, N=%d).", self._db_path, self.min_repeats)
        except Exception as e:  # noqa: BLE001
            logger.warning("[SKILL] store init failed, skills disabled: %s", e)
            self.enabled = False

    # -- schema ---------------------------------------------------------- #
    def _init_schema(self) -> None:
        c = self._conn
        c.execute(
            """CREATE TABLE IF NOT EXISTS skills(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger TEXT NOT NULL,
                steps_sig TEXT NOT NULL,
                steps TEXT NOT NULL,
                verified INTEGER DEFAULT 1,
                success_count INTEGER DEFAULT 0,
                fail_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_used TEXT,
                UNIQUE(trigger, steps_sig))"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS skill_observations(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger TEXT NOT NULL,
                steps_sig TEXT NOT NULL,
                steps TEXT NOT NULL,
                count INTEGER DEFAULT 0,
                updated_at TEXT NOT NULL,
                UNIQUE(trigger, steps_sig))"""
        )
        c.commit()

    # -- helpers --------------------------------------------------------- #
    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def normalize_trigger(text: Any) -> str:
        """Normalize a command into a stable key: lowercase, collapse spaces,
        drop surrounding/trailing punctuation. Keeps it generic (no hardcoded
        command list)."""
        s = str(text or "").strip().lower()
        s = " ".join(s.split())
        return s.strip(" .!?,;:\"'")

    @staticmethod
    def _steps_sig(trigger: str, steps: Any) -> str:
        try:
            steps_json = json.dumps(steps, sort_keys=True, default=str)
        except Exception:  # noqa: BLE001
            steps_json = str(steps)
        raw = (trigger + "|" + steps_json).encode("utf-8", errors="ignore")
        return hashlib.sha1(raw).hexdigest()  # noqa: S324 - non-crypto signature

    @staticmethod
    def _steps_json(steps: Any) -> str:
        try:
            return json.dumps(steps, default=str)[:4000]
        except Exception:  # noqa: BLE001
            return str(steps)[:4000]

    # -- writes ---------------------------------------------------------- #
    def record_verified_success(
        self, trigger: Any, steps: Any, min_repeats: Optional[int] = None
    ) -> dict:
        """Log one verified success. Crystallizes into a real skill once the same
        (trigger, steps) has succeeded >= N times.

        Returns {"crystallized": bool, "repeats": int, "skill_id": int|None}.
        Never raises.
        """
        result = {"crystallized": False, "repeats": 0, "skill_id": None}
        if not self.enabled or not self._conn:
            return result
        need = max(1, int(min_repeats if min_repeats is not None else self.min_repeats))
        try:
            trig = self.normalize_trigger(trigger)
            if not trig:
                return result
            sig = self._steps_sig(trig, steps)
            steps_json = self._steps_json(steps)
            now = self._now()
            with self._lock:
                c = self._conn
                # 1) bump the observation counter (fluke-guard)
                row = c.execute(
                    "SELECT count FROM skill_observations WHERE trigger=? AND steps_sig=?",
                    (trig, sig),
                ).fetchone()
                if row:
                    count = int(row[0]) + 1
                    c.execute(
                        "UPDATE skill_observations SET count=?, updated_at=? "
                        "WHERE trigger=? AND steps_sig=?",
                        (count, now, trig, sig),
                    )
                else:
                    count = 1
                    c.execute(
                        "INSERT INTO skill_observations(trigger, steps_sig, steps, count, updated_at) "
                        "VALUES(?,?,?,?,?)",
                        (trig, sig, steps_json, count, now),
                    )
                result["repeats"] = count

                # 2) crystallize once the repeat threshold is reached
                if count >= need:
                    existing = c.execute(
                        "SELECT id, success_count FROM skills WHERE trigger=? AND steps_sig=?",
                        (trig, sig),
                    ).fetchone()
                    if existing:
                        sid = existing[0]
                        c.execute(
                            "UPDATE skills SET success_count=?, verified=1, status='active', "
                            "updated_at=?, last_used=? WHERE id=?",
                            (count, now, now, sid),
                        )
                        result["skill_id"] = sid
                        result["crystallized"] = False  # already existed
                    else:
                        cur = c.execute(
                            "INSERT INTO skills(trigger, steps_sig, steps, verified, "
                            "success_count, fail_count, status, created_at, updated_at, last_used) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (trig, sig, steps_json, 1, count, 0, "active", now, now, now),
                        )
                        result["skill_id"] = cur.lastrowid
                        result["crystallized"] = True
                        logger.info(
                            "[SKILL] crystallized new skill: '%s' (after %d verified successes).",
                            trig, count,
                        )
                c.commit()
            return result
        except Exception as e:  # noqa: BLE001
            logger.debug("[SKILL] record_verified_success failed: %s", e)
            return result

    def record_failure(self, trigger: Any, steps: Any = None) -> None:
        """Note a post-crystallization failure. Enough failures retire the skill
        (status -> 'stale') so it stops being trusted."""
        if not self.enabled or not self._conn:
            return
        try:
            trig = self.normalize_trigger(trigger)
            if not trig:
                return
            now = self._now()
            with self._lock:
                c = self._conn
                rows = c.execute(
                    "SELECT id, fail_count, success_count FROM skills WHERE trigger=?",
                    (trig,),
                ).fetchall()
                for sid, fail_count, success_count in rows:
                    fc = int(fail_count or 0) + 1
                    status = "stale" if fc >= max(2, int(success_count or 0)) else "active"
                    c.execute(
                        "UPDATE skills SET fail_count=?, status=?, updated_at=? WHERE id=?",
                        (fc, status, now, sid),
                    )
                c.commit()
        except Exception as e:  # noqa: BLE001
            logger.debug("[SKILL] record_failure failed: %s", e)

    def touch(self, trigger: Any) -> None:
        """Mark a skill as just used (updates last_used)."""
        if not self.enabled or not self._conn:
            return
        try:
            trig = self.normalize_trigger(trigger)
            now = self._now()
            with self._lock:
                self._conn.execute(
                    "UPDATE skills SET last_used=? WHERE trigger=?", (now, trig)
                )
                self._conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.debug("[SKILL] touch failed: %s", e)

    # -- reads ----------------------------------------------------------- #
    def get_skill(self, trigger: Any) -> Optional[dict]:
        """Return the active skill row for a trigger, or None."""
        if not self.enabled or not self._conn:
            return None
        try:
            trig = self.normalize_trigger(trigger)
            with self._lock:
                row = self._conn.execute(
                    "SELECT id, trigger, steps, verified, success_count, fail_count, "
                    "status, last_used FROM skills WHERE trigger=? AND status='active' "
                    "ORDER BY success_count DESC LIMIT 1",
                    (trig,),
                ).fetchone()
            if not row:
                return None
            return {
                "id": row[0], "trigger": row[1], "steps": self._loads(row[2]),
                "verified": bool(row[3]), "success_count": row[4],
                "fail_count": row[5], "status": row[6], "last_used": row[7],
            }
        except Exception as e:  # noqa: BLE001
            logger.debug("[SKILL] get_skill failed: %s", e)
            return None

    def list_skills(self, limit: int = 50, only_active: bool = True) -> List[dict]:
        if not self.enabled or not self._conn:
            return []
        try:
            q = (
                "SELECT trigger, success_count, fail_count, status, last_used FROM skills "
                + ("WHERE status='active' " if only_active else "")
                + "ORDER BY success_count DESC LIMIT ?"
            )
            with self._lock:
                rows = self._conn.execute(q, (int(limit),)).fetchall()
            return [
                {"trigger": r[0], "success_count": r[1], "fail_count": r[2],
                 "status": r[3], "last_used": r[4]}
                for r in rows
            ]
        except Exception as e:  # noqa: BLE001
            logger.debug("[SKILL] list_skills failed: %s", e)
            return []

    @staticmethod
    def _loads(s: Any) -> Any:
        try:
            return json.loads(s)
        except Exception:  # noqa: BLE001
            return s

    def stats(self) -> dict:
        if not self.enabled or not self._conn:
            return {"enabled": False, "skills": 0, "observations": 0}
        try:
            with self._lock:
                skills = self._conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0]
                obs = self._conn.execute("SELECT COUNT(*) FROM skill_observations").fetchone()[0]
            return {"enabled": True, "skills": skills, "observations": obs}
        except Exception:  # noqa: BLE001
            return {"enabled": False, "skills": 0, "observations": 0}
