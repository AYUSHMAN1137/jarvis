"""Phase 8 -- User model (personalization).

A small, fail-soft SQLite store of what JARVIS has learned about ONE user:
  * facts    -- explicit things to remember (name, city, preferences)
  * aliases  -- "my editor" -> "vs code"
  * habits   -- sequence patterns learned from real, successful actions
                ("after opening <X>, you usually run <tool>"). Only patterns
                seen >= HABIT_MIN_OBSERVATIONS times are trusted.

Nothing is hardcoded: every habit is aggregated at runtime from the memory
action log (injected `action_provider` so this is unit-testable without the
real memory service). Phase 8 then feeds:
  * Phase 7 (proactive) via `habits_for(context)` -- the habit provider.
  * Brain / agent prompts via `augment(system_message)`.
  * the dashboard via `knowledge_summary()` + `forget_*` controls.

Everything is fail-soft; a personalization miss never breaks a turn.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Callable, Dict, List, Optional

import config as _cfg

logger = logging.getLogger("J.A.R.V.I.S")

_ENABLED = bool(getattr(_cfg, "PHASE8_ENABLED", True))
_DB_PATH = getattr(_cfg, "USER_MODEL_DB_PATH", None)
_MIN_OBS = int(getattr(_cfg, "HABIT_MIN_OBSERVATIONS", 3))


# Sentinel so callers can request an isolated in-memory DB by passing
# db_path=None, while the default (db_path unset) uses the configured file.
_UNSET = object()


def _norm(text: Any) -> str:
    s = str(text or "").strip().lower()
    return " ".join(s.split())


class UserModel:
    def __init__(
        self,
        db_path: Any = _UNSET,
        action_provider: Optional[Callable[[], List[dict]]] = None,
        min_observations: Optional[int] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.enabled = _ENABLED
        self.started = False
        # db_path unset -> configured file; explicit None -> isolated :memory:;
        # explicit string -> that file.
        self._db_path = _DB_PATH if db_path is _UNSET else db_path
        self._action_provider = action_provider
        self.min_observations = _MIN_OBS if min_observations is None else int(min_observations)
        self._clock = clock or time.time
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()

    # -- lifecycle ------------------------------------------------------- #
    def start(self) -> None:
        if not self.enabled:
            logger.info("[USERMODEL] Phase 8 disabled (PHASE8_ENABLED=False).")
            return
        if self.started:
            return
        try:
            self._init_db()
            try:
                from app.services.agent.checker.event_bus import get_event_bus
                get_event_bus().subscribe("verified", self._on_verified)
            except Exception as e:  # noqa: BLE001
                logger.debug("[USERMODEL] live verified subscription skipped: %s", e)
            self.started = True
            logger.info("[USERMODEL] Phase 8 online -- min_observations=%d.", self.min_observations)
        except Exception as e:  # noqa: BLE001
            logger.warning("[USERMODEL] Phase 8 failed to start (non-fatal): %s", e)
            self.started = False

    def stop(self) -> None:
        try:
            if self._conn is not None:
                self._conn.close()
        except Exception:  # noqa: BLE001
            pass
        self._conn = None

    def _init_db(self) -> None:
        if not self._db_path:
            # In-memory fallback keeps the model usable even with no path.
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        else:
            try:
                d = os.path.dirname(str(self._db_path))
                if d:
                    os.makedirs(d, exist_ok=True)
            except Exception:  # noqa: BLE001
                pass
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        c = self._conn
        c.execute(
            "CREATE TABLE IF NOT EXISTS um_facts ("
            "key TEXT PRIMARY KEY, value TEXT, source TEXT, "
            "created_at REAL, updated_at REAL)"
        )
        c.execute(
            "CREATE TABLE IF NOT EXISTS um_aliases ("
            "alias TEXT PRIMARY KEY, canonical TEXT, created_at REAL)"
        )
        c.execute(
            "CREATE TABLE IF NOT EXISTS um_habits ("
            "context TEXT, action TEXT, args TEXT, count INTEGER DEFAULT 0, "
            "last_seen REAL, PRIMARY KEY(context, action))"
        )
        c.execute("CREATE TABLE IF NOT EXISTS um_meta (key TEXT PRIMARY KEY, value TEXT)")
        c.commit()

    # -- facts ----------------------------------------------------------- #
    def set_fact(self, key: str, value: str, source: str = "user") -> bool:
        key = _norm(key)
        if not key or self._conn is None:
            return False
        try:
            now = self._clock()
            with self._lock:
                self._conn.execute(
                    "INSERT INTO um_facts(key, value, source, created_at, updated_at) "
                    "VALUES(?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
                    "value=excluded.value, source=excluded.source, updated_at=excluded.updated_at",
                    (key, str(value), source, now, now),
                )
                self._conn.commit()
            return True
        except Exception as e:  # noqa: BLE001
            logger.debug("[USERMODEL] set_fact failed: %s", e)
            return False

    def get_fact(self, key: str) -> Optional[str]:
        key = _norm(key)
        if not key or self._conn is None:
            return None
        try:
            row = self._conn.execute(
                "SELECT value FROM um_facts WHERE key=?", (key,)
            ).fetchone()
            return row[0] if row else None
        except Exception:  # noqa: BLE001
            return None

    def all_facts(self) -> List[dict]:
        if self._conn is None:
            return []
        try:
            rows = self._conn.execute(
                "SELECT key, value, source, updated_at FROM um_facts ORDER BY updated_at DESC"
            ).fetchall()
            return [{"key": r[0], "value": r[1], "source": r[2], "updated_at": r[3]} for r in rows]
        except Exception:  # noqa: BLE001
            return []

    def forget_fact(self, key: str) -> bool:
        key = _norm(key)
        if not key or self._conn is None:
            return False
        try:
            with self._lock:
                cur = self._conn.execute("DELETE FROM um_facts WHERE key=?", (key,))
                self._conn.commit()
            return cur.rowcount > 0
        except Exception:  # noqa: BLE001
            return False

    # -- aliases --------------------------------------------------------- #
    def set_alias(self, alias: str, canonical: str) -> bool:
        alias = _norm(alias)
        canonical = _norm(canonical)
        if not alias or not canonical or self._conn is None:
            return False
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO um_aliases(alias, canonical, created_at) VALUES(?,?,?) "
                    "ON CONFLICT(alias) DO UPDATE SET canonical=excluded.canonical",
                    (alias, canonical, self._clock()),
                )
                self._conn.commit()
            return True
        except Exception:  # noqa: BLE001
            return False

    def resolve_alias(self, alias: str) -> Optional[str]:
        alias = _norm(alias)
        if not alias or self._conn is None:
            return None
        try:
            row = self._conn.execute(
                "SELECT canonical FROM um_aliases WHERE alias=?", (alias,)
            ).fetchone()
            return row[0] if row else None
        except Exception:  # noqa: BLE001
            return None

    def all_aliases(self) -> List[dict]:
        if self._conn is None:
            return []
        try:
            rows = self._conn.execute(
                "SELECT alias, canonical FROM um_aliases ORDER BY alias"
            ).fetchall()
            return [{"alias": r[0], "canonical": r[1]} for r in rows]
        except Exception:  # noqa: BLE001
            return []

    def forget_alias(self, alias: str) -> bool:
        alias = _norm(alias)
        if not alias or self._conn is None:
            return False
        try:
            with self._lock:
                cur = self._conn.execute("DELETE FROM um_aliases WHERE alias=?", (alias,))
                self._conn.commit()
            return cur.rowcount > 0
        except Exception:  # noqa: BLE001
            return False

    # -- habits ---------------------------------------------------------- #
    @staticmethod
    def _ctx_of(action: dict) -> str:
        """Context key a habit is anchored on = what the PREVIOUS action acted
        on (its target), else the tool name."""
        return _norm(action.get("target") or action.get("tool") or "")

    def observe(self, actions: List[dict]) -> int:
        """Aggregate sequential habits from a list of action dicts
        ({tool, target, args, ok, created_at}). Returns #pairs observed.
        Learns "after <prev.target>, you run <cur.tool>"."""
        if self._conn is None or not actions:
            return 0
        try:
            acts = [a for a in actions if isinstance(a, dict) and (a.get("ok", 1) in (1, True))]
            acts.sort(key=lambda a: str(a.get("created_at") or ""))
            pairs = 0
            for i in range(1, len(acts)):
                context = self._ctx_of(acts[i - 1])
                cur = acts[i]
                action = _norm(cur.get("tool"))
                if not context or not action:
                    continue
                args = cur.get("args")
                if not isinstance(args, str):
                    try:
                        args = json.dumps(args, default=str)[:500]
                    except Exception:  # noqa: BLE001
                        args = ""
                self._bump_habit(context, action, args)
                pairs += 1
            return pairs
        except Exception as e:  # noqa: BLE001
            logger.debug("[USERMODEL] observe failed: %s", e)
            return 0

    def _bump_habit(self, context: str, action: str, args: str) -> None:
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO um_habits(context, action, args, count, last_seen) "
                    "VALUES(?,?,?,1,?) ON CONFLICT(context, action) DO UPDATE SET "
                    "count=count+1, args=excluded.args, last_seen=excluded.last_seen",
                    (context, action, args, self._clock()),
                )
                self._conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.debug("[USERMODEL] bump_habit failed: %s", e)

    def aggregate_from_provider(self) -> int:
        """Pull only new actions and advance a durable ingestion watermark."""
        if self._action_provider is None:
            return 0
        try:
            actions = self._action_provider() or []
        except Exception as e:  # noqa: BLE001
            logger.debug("[USERMODEL] action_provider failed: %s", e)
            return 0
        try:
            row = self._conn.execute(
                "SELECT value FROM um_meta WHERE key='last_action_id'"
            ).fetchone()
            cursor = int(row[0]) if row and str(row[0]).isdigit() else 0
            new_actions = [a for a in actions if int(a.get("id") or 0) > cursor]
            # Learn only verified success when verdict data exists. Legacy rows
            # without a verdict remain eligible by their historical ok flag.
            eligible = [a for a in new_actions if a.get("verification_verdict") in (None, "", "PASS")]
            prev_row = self._conn.execute(
                "SELECT value FROM um_meta WHERE key='last_eligible_action'"
            ).fetchone()
            previous = None
            if prev_row and prev_row[0]:
                try:
                    previous = json.loads(prev_row[0])
                except Exception:  # noqa: BLE001
                    previous = None
            sequence = ([previous] if isinstance(previous, dict) else []) + eligible
            observed = self.observe(sequence)
            max_id = max([int(a.get("id") or 0) for a in new_actions] or [cursor])
            with self._lock:
                self._conn.execute(
                    "INSERT INTO um_meta(key,value) VALUES('last_action_id',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(max_id),)
                )
                if eligible:
                    self._conn.execute(
                        "INSERT INTO um_meta(key,value) VALUES('last_eligible_action',?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (json.dumps(eligible[-1], default=str)[:2000],),
                    )
                self._conn.commit()
            return observed
        except Exception as e:  # noqa: BLE001
            logger.debug("[USERMODEL] incremental aggregation failed: %s", e)
            return 0

    def _on_verified(self, payload: dict) -> None:
        """Incrementally consume newly verified memory actions exactly once."""
        try:
            self.aggregate_from_provider()
        except Exception as e:  # noqa: BLE001
            logger.debug("[USERMODEL] live aggregation failed: %s", e)

    def habits_for(self, context: Any, payload: dict = None, limit: int = 3) -> List[dict]:
        """Return trusted next-action habits for a context (fuzzy contains
        match). Shape matches Phase 7's habit_provider contract."""
        ctx = _norm(context)
        if not ctx or self._conn is None:
            return []
        try:
            rows = self._conn.execute(
                "SELECT context, action, args, count FROM um_habits "
                "WHERE count >= ? ORDER BY count DESC",
                (self.min_observations,),
            ).fetchall()
        except Exception:  # noqa: BLE001
            return []
        out: List[dict] = []
        for context_key, action, args, count in rows:
            ck = context_key or ""
            if not ck:
                continue
            if ck in ctx or ctx in ck:
                try:
                    parsed = json.loads(args) if args else {}
                except Exception:  # noqa: BLE001
                    parsed = {}
                out.append({
                    "action": action,
                    "args": parsed,
                    "command": {"tool": action, "args": parsed},
                    "count": count,
                    "confidence": round(min(1.0, count / float(count + 2)), 3),
                    "text": "You usually %s after this \u2014 want me to?" % action.replace("_", " "),
                })
                if len(out) >= limit:
                    break
        return out

    def top_habits(self, limit: int = 10) -> List[dict]:
        if self._conn is None:
            return []
        try:
            rows = self._conn.execute(
                "SELECT context, action, count, last_seen FROM um_habits "
                "ORDER BY count DESC LIMIT ?", (int(limit),),
            ).fetchall()
            return [{"context": r[0], "action": r[1], "count": r[2],
                     "trusted": r[2] >= self.min_observations, "last_seen": r[3]}
                    for r in rows]
        except Exception:  # noqa: BLE001
            return []

    def forget_habit(self, context: str, action: str = None) -> bool:
        context = _norm(context)
        if not context or self._conn is None:
            return False
        try:
            with self._lock:
                if action:
                    cur = self._conn.execute(
                        "DELETE FROM um_habits WHERE context=? AND action=?",
                        (context, _norm(action)),
                    )
                else:
                    cur = self._conn.execute(
                        "DELETE FROM um_habits WHERE context=?", (context,)
                    )
                self._conn.commit()
            return cur.rowcount > 0
        except Exception:  # noqa: BLE001
            return False

    def forget_all(self) -> bool:
        """Wipe everything JARVIS has learned about the user (privacy control)."""
        if self._conn is None:
            return False
        try:
            with self._lock:
                for tbl in ("um_facts", "um_aliases", "um_habits", "um_meta"):
                    self._conn.execute("DELETE FROM %s" % tbl)
                self._conn.commit()
            return True
        except Exception:  # noqa: BLE001
            return False

    # -- prompt + dashboard feeds --------------------------------------- #
    def knowledge_summary(self, max_facts: int = 8, max_habits: int = 6) -> dict:
        """Structured "what JARVIS knows about you" for the dashboard."""
        return {
            "facts": self.all_facts()[:max_facts],
            "aliases": self.all_aliases(),
            "habits": [h for h in self.top_habits(max_habits) if h["trusted"]],
        }

    def augment(self, system_message: str) -> str:
        """Append a compact personalization block to a system prompt."""
        try:
            facts = self.all_facts()[:6]
            habits = [h for h in self.top_habits(5) if h["trusted"]]
            if not facts and not habits:
                return system_message
            lines = ["", "What you know about the user:"]
            for f in facts:
                lines.append("- %s: %s" % (f["key"], f["value"]))
            for h in habits:
                lines.append("- Habit: after %s, usually %s" % (h["context"], h["action"]))
            return system_message + "\n".join(lines)
        except Exception:  # noqa: BLE001
            return system_message

    def stats(self) -> dict:
        out = {"enabled": self.enabled, "started": self.started,
               "min_observations": self.min_observations,
               "facts": 0, "aliases": 0, "habits": 0, "trusted_habits": 0}
        if self._conn is None:
            return out
        try:
            out["facts"] = self._conn.execute("SELECT COUNT(*) FROM um_facts").fetchone()[0]
            out["aliases"] = self._conn.execute("SELECT COUNT(*) FROM um_aliases").fetchone()[0]
            out["habits"] = self._conn.execute("SELECT COUNT(*) FROM um_habits").fetchone()[0]
            out["trusted_habits"] = self._conn.execute(
                "SELECT COUNT(*) FROM um_habits WHERE count >= ?", (self.min_observations,)
            ).fetchone()[0]
        except Exception:  # noqa: BLE001
            pass
        return out

    def health(self) -> dict:
        return {"enabled": self.enabled, "started": self.started,
                "db": self._conn is not None}


# --------------------------------------------------------------------------- #
# singleton
# --------------------------------------------------------------------------- #
_model: Optional[UserModel] = None
_model_lock = threading.Lock()


def _default_action_provider() -> List[dict]:
    """Pull the recent action log from the memory service (best-effort)."""
    try:
        from app.services.memory_service import get_memory
        mem = get_memory()
        conn = getattr(mem, "_conn", None)
        if conn is None:
            return []
        cols = {r[1] for r in conn.execute("PRAGMA table_info(actions)").fetchall()}
        verdict_expr = "verification_verdict" if "verification_verdict" in cols else "NULL"
        rows = conn.execute(
            f"SELECT id, tool, target, args, ok, created_at, {verdict_expr} "
            "FROM actions ORDER BY id ASC"
        ).fetchall()
        return [{"id": r[0], "tool": r[1], "target": r[2], "args": r[3],
                 "ok": r[4], "created_at": r[5], "verification_verdict": r[6]}
                for r in rows]
    except Exception:  # noqa: BLE001
        return []


def get_phase8() -> UserModel:
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                _model = UserModel(action_provider=_default_action_provider)
    return _model
