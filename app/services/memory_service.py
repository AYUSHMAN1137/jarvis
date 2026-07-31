"""
Persistent memory store for J.A.R.V.I.S  (Master Plan -- Phase 2).

Goal (Master Plan, Phase 2): give JARVIS a memory so it "yaad rakhe" -- the
short-term session is already handled by chat_service; THIS module is the
PERMANENT layer.

Design rules (straight from the Master Plan):
  * Reliability #1 -- every public method is fail-soft. A memory problem must
    NEVER crash a chat reply or an action. On any error we log + return a safe
    default, so JARVIS simply behaves as if it had no memory for that call.
  * Speed #2       -- local SQLite (WAL) + a tiny always-injected profile block.
    No network, no embeddings, no heavy ML on the hot path.
  * Privacy        -- everything is local. Obvious secrets (passwords, OTPs,
    API keys, card numbers) are never written to disk.
  * No hardcode    -- nothing about a specific user is baked in; the profile
    lives in human-editable markdown files the user fully controls.

What it stores
  1. facts       -- durable facts/preferences ("name = Ayush", "default browser
                    = Brave"). Structured rows, upserted by key.
  2. actions     -- a short rolling log of what JARVIS did (last opened app,
                    etc.) so later phases can resolve "usko band karo".
  3. corrections -- "nahi, aise nahi" -> remembered so the same mistake is not
                    repeated (Master Plan add-on: Correction memory).
  4. profile md  -- data/memory/user_profile.md + jarvis_persona.md, small
                    human-readable files injected into every prompt.

Recall
  * Always-inject : profile md + top facts + last action + recent corrections
                    -> get_prompt_context() (small, cheap, every turn).
  * On-demand     : recall(query) -> SQLite FTS5 keyword search, with a LIKE
                    fallback if FTS5 is unavailable. Exposed as a tool.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
from datetime import datetime, date

from config import (
    ASSISTANT_NAME,
    JARVIS_OWNER_NAME,
    MEMORY_ENABLED,
    MEMORY_DB_PATH,
    USER_PROFILE_PATH,
    JARVIS_PERSONA_PATH,
    MEMORY_DIR,
    MEMORY_CONTEXT_MAX_CHARS,
    MEMORY_MAX_FACTS_INJECT,
    MEMORY_MAX_ACTIONS,
    MEMORY_REDACT_SECRETS,
)

logger = logging.getLogger("J.A.R.V.I.S")

_VALID_CATEGORIES = ("user", "preference", "project", "feedback", "general")

# Args keys we treat as the "target" of an action (for last-action memory).
_TARGET_KEYS = (
    "name", "app", "application", "query", "url", "website", "site",
    "path", "title", "text", "folder", "file", "window",
)

# Obvious secrets we refuse to persist.
_SECRET_HINTS = re.compile(
    r"(password|passwd|\bpwd\b|secret|api[ _-]?key|access[ _-]?token|"
    r"\botp\b|\bcvv\b|\bpin\b|private key|seed phrase)",
    re.IGNORECASE,
)


class MemoryService:
    """Local, fail-soft persistent memory. All public methods swallow errors."""

    def __init__(self) -> None:
        self.enabled = bool(MEMORY_ENABLED)
        self._fts = False
        self._lock = threading.RLock()
        self._conn = None
        if not self.enabled:
            logger.info("[MEMORY] Disabled via config (MEMORY_ENABLED=false).")
            return
        try:
            self._ensure_files()
            from app.services.db import open_db
            self._conn = open_db(MEMORY_DB_PATH, label="memory")
            self._init_schema()
            logger.info("[MEMORY] Ready (%s).", self.status())
        except Exception as e:  # noqa: BLE001
            logger.warning("[MEMORY] init failed, memory disabled: %s", e)
            self.enabled = False

    # ------------------------------------------------------------------ #
    # schema + files
    # ------------------------------------------------------------------ #
    def _init_schema(self) -> None:
        c = self._conn
        c.execute(
            """CREATE TABLE IF NOT EXISTS facts(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL DEFAULT 'general',
                key TEXT,
                value TEXT NOT NULL,
                source TEXT DEFAULT 'user',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL)"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS actions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool TEXT,
                target TEXT,
                args TEXT,
                ok INTEGER DEFAULT 1,
                created_at TEXT NOT NULL)"""
        )
        # Add execution correlation without destroying existing user data.
        existing = {r[1] for r in c.execute("PRAGMA table_info(actions)").fetchall()}
        missing = {name for name in (
            "execution_id", "action_id", "verification_verdict",
            "verification_source", "context_key"
        ) if name not in existing}
        if missing:
            try:
                backup_path = str(MEMORY_DB_PATH) + ".pre-v2.bak"
                if str(MEMORY_DB_PATH) != ":memory:" and not os.path.exists(backup_path):
                    backup_conn = sqlite3.connect(backup_path)
                    c.backup(backup_conn)
                    backup_conn.close()
                    logger.info("[MEMORY] pre-migration backup created: %s", backup_path)
            except Exception as e:  # noqa: BLE001
                logger.warning("[MEMORY] pre-migration backup failed; migration continues additively: %s", e)
        for column, sql_type in (
            ("execution_id", "TEXT"), ("action_id", "TEXT"),
            ("verification_verdict", "TEXT"), ("verification_source", "TEXT"),
            ("context_key", "TEXT"),
        ):
            if column not in existing:
                c.execute(f"ALTER TABLE actions ADD COLUMN {column} {sql_type}")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_actions_action_id "
                  "ON actions(action_id) WHERE action_id IS NOT NULL AND action_id != ''")
        c.execute(
            """CREATE TABLE IF NOT EXISTS corrections(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wrong TEXT,
                right TEXT,
                context TEXT,
                created_at TEXT NOT NULL)"""
        )
        try:
            c.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS mem_fts "
                "USING fts5(text, ref_type UNINDEXED, ref_id UNINDEXED)"
            )
            self._fts = True
        except Exception as e:  # noqa: BLE001 - FTS5 optional; LIKE fallback exists
            logger.info("[MEMORY] FTS5 unavailable, using LIKE search: %s", e)
            self._fts = False
        c.commit()

    def _ensure_files(self) -> None:
        try:
            MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001
            pass
        owner = (JARVIS_OWNER_NAME or "").strip()
        today = date.today().isoformat()
        if not USER_PROFILE_PATH.exists():
            seed = (
                "---\n"
                f"name: {owner}\n"
                "language:\n"
                f"created: {today}\n"
                "---\n"
                "# About the user\n"
                f"{ASSISTANT_NAME} reads this every conversation. Add durable facts here.\n"
                "- Example: how they like to be addressed, default browser, favourite music.\n"
            )
            try:
                USER_PROFILE_PATH.write_text(seed, encoding="utf-8")
            except Exception as e:  # noqa: BLE001
                logger.debug("[MEMORY] could not seed profile: %s", e)
        if not JARVIS_PERSONA_PATH.exists():
            persona = (
                "---\n"
                f"assistant_name: {ASSISTANT_NAME}\n"
                "---\n"
                f"# {ASSISTANT_NAME} persona notes\n"
                "- Optional. Add tone/behaviour tweaks here; read every conversation.\n"
            )
            try:
                JARVIS_PERSONA_PATH.write_text(persona, encoding="utf-8")
            except Exception as e:  # noqa: BLE001
                logger.debug("[MEMORY] could not seed persona: %s", e)

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    def _looks_secret(self, text: str) -> bool:
        if not MEMORY_REDACT_SECRETS:
            return False
        if _SECRET_HINTS.search(text):
            return True
        # long opaque token (likely a key) or card-like digit run
        if re.search(r"\b[A-Za-z0-9_\-]{24,}\b", text):
            return True
        if re.search(r"\b(?:\d[ -]?){13,16}\b", text):
            return True
        return False

    def _fts_index(self, ref_type: str, ref_id: int, text: str) -> None:
        if not self._fts or not text:
            return
        try:
            self._conn.execute(
                "DELETE FROM mem_fts WHERE ref_type=? AND ref_id=?", (ref_type, ref_id)
            )
            self._conn.execute(
                "INSERT INTO mem_fts(text, ref_type, ref_id) VALUES(?,?,?)",
                (text, ref_type, ref_id),
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("[MEMORY] fts index failed: %s", e)

    @staticmethod
    def _extract_target(args: dict) -> str:
        if not isinstance(args, dict):
            return ""
        for k in _TARGET_KEYS:
            v = args.get(k)
            if v:
                return str(v)[:80]
        return ""

    def status(self) -> str:
        if not self.enabled or not self._conn:
            return "disabled"
        try:
            with self._lock:
                n = self._conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
            return f"facts={n}, fts={'on' if self._fts else 'off'}"
        except Exception:  # noqa: BLE001
            return "error"

    # ------------------------------------------------------------------ #
    # writes
    # ------------------------------------------------------------------ #
    def remember(self, value, category: str = "general", key=None, source: str = "user") -> str:
        if not self.enabled or not self._conn:
            return "My long-term memory isn't available right now."
        try:
            value = str(value or "").strip()
            if not value:
                return "There was nothing to remember."
            if len(value) > 400:
                value = value[:400].rstrip()
            if self._looks_secret(value):
                logger.info("[MEMORY] refused to store likely-secret value.")
                return "I won't save that \u2014 it looks like a password or secret."
            category = category if category in _VALID_CATEGORIES else "general"
            key = (str(key).strip() or None) if key else None
            now = self._now()
            with self._lock:
                c = self._conn
                fid = None
                if key:
                    row = c.execute(
                        "SELECT id FROM facts WHERE category=? AND key=?", (category, key)
                    ).fetchone()
                    if row:
                        fid = row[0]
                        c.execute(
                            "UPDATE facts SET value=?, source=?, updated_at=? WHERE id=?",
                            (value, source, now, fid),
                        )
                else:
                    row = c.execute(
                        "SELECT id FROM facts WHERE category=? AND value=?", (category, value)
                    ).fetchone()
                    if row:
                        fid = row[0]
                        c.execute("UPDATE facts SET updated_at=? WHERE id=?", (now, fid))
                if fid is None:
                    cur = c.execute(
                        "INSERT INTO facts(category, key, value, source, created_at, updated_at) "
                        "VALUES(?,?,?,?,?,?)",
                        (category, key, value, source, now, now),
                    )
                    fid = cur.lastrowid
                display = f"{key}: {value}" if key else value
                self._fts_index("fact", fid, display)
                c.commit()
            return "Got it, I'll remember that."
        except Exception as e:  # noqa: BLE001
            logger.warning("[MEMORY] remember failed: %s", e)
            return "I couldn't save that to memory."

    def record_action(self, tool: str, args, ok: bool = True,
                      execution_id: str = "", action_id: str = "",
                      context_key: str = "") -> None:
        if not self.enabled or not self._conn:
            return
        try:
            target = self._extract_target(args if isinstance(args, dict) else {})
            try:
                args_json = json.dumps(args, default=str)[:500]
            except Exception:  # noqa: BLE001
                args_json = ""
            now = self._now()
            with self._lock:
                c = self._conn
                if action_id:
                    cur = c.execute(
                        "INSERT OR IGNORE INTO actions(tool,target,args,ok,created_at,execution_id,action_id,context_key) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (str(tool), target, args_json, 1 if ok else 0, now,
                         execution_id or "", action_id, context_key or ""),
                    )
                    if cur.rowcount == 0:
                        return
                else:
                    cur = c.execute(
                        "INSERT INTO actions(tool,target,args,ok,created_at,execution_id,action_id,context_key) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (str(tool), target, args_json, 1 if ok else 0, now,
                         execution_id or "", None, context_key or ""),
                    )
                aid = cur.lastrowid
                text = f"used {tool}" + (f" on {target}" if target else "")
                self._fts_index("action", aid, text)
                # Keep only the most recent N actions in the hot table, but move
                # the older ones to actions_archive instead of dropping them --
                # this log is the raw material Phase 8 learns habits from, and
                # once deleted that history cannot be rebuilt.
                overflow = (
                    "SELECT id FROM actions WHERE id NOT IN "
                    "(SELECT id FROM actions ORDER BY id DESC LIMIT ?)"
                )
                try:
                    c.execute("CREATE TABLE IF NOT EXISTS actions_archive "
                              "AS SELECT * FROM actions WHERE 0")
                    c.execute(f"INSERT INTO actions_archive SELECT * FROM actions "
                              f"WHERE id IN ({overflow})", (MEMORY_MAX_ACTIONS,))
                except Exception as _arch:  # noqa: BLE001 - archiving is best-effort
                    logger.debug("[MEMORY] action archive skipped: %s", _arch)
                c.execute(f"DELETE FROM actions WHERE id IN ({overflow})",
                          (MEMORY_MAX_ACTIONS,))
                if self._fts:
                    c.execute(
                        "DELETE FROM mem_fts WHERE ref_type='action' AND ref_id NOT IN "
                        "(SELECT id FROM actions)"
                    )
                c.commit()
        except Exception as e:  # noqa: BLE001
            logger.debug("[MEMORY] record_action failed: %s", e)

    def update_action_verification(self, action_id: str, verdict: str,
                                   source: str = "") -> None:
        if not self.enabled or not self._conn or not action_id:
            return
        try:
            with self._lock:
                self._conn.execute(
                    "UPDATE actions SET verification_verdict=?, verification_source=? "
                    "WHERE action_id=?", (str(verdict), str(source), str(action_id))
                )
                self._conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.debug("[MEMORY] verification update failed: %s", e)

    def record_correction(self, wrong, right, context: str = "") -> str:
        if not self.enabled or not self._conn:
            return ""
        try:
            wrong = str(wrong or "").strip()[:200]
            right = str(right or "").strip()[:200]
            if not right:
                return ""
            now = self._now()
            with self._lock:
                c = self._conn
                cur = c.execute(
                    "INSERT INTO corrections(wrong, right, context, created_at) VALUES(?,?,?,?)",
                    (wrong, right, str(context)[:200], now),
                )
                cid = cur.lastrowid
                text = f"correction: do '{right}' not '{wrong}'"
                self._fts_index("correction", cid, text)
                c.commit()
            return "Noted \u2014 I won't make that mistake again."
        except Exception as e:  # noqa: BLE001
            logger.debug("[MEMORY] record_correction failed: %s", e)
            return ""

    def forget(self, query) -> int:
        if not self.enabled or not self._conn:
            return 0
        try:
            q = str(query or "").strip()
            if not q:
                return 0
            like = f"%{q}%"
            with self._lock:
                c = self._conn
                rows = c.execute(
                    "SELECT id FROM facts WHERE value LIKE ? OR key LIKE ?", (like, like)
                ).fetchall()
                ids = [r[0] for r in rows]
                for fid in ids:
                    c.execute("DELETE FROM facts WHERE id=?", (fid,))
                    if self._fts:
                        c.execute(
                            "DELETE FROM mem_fts WHERE ref_type='fact' AND ref_id=?", (fid,)
                        )
                c.commit()
            return len(ids)
        except Exception as e:  # noqa: BLE001
            logger.debug("[MEMORY] forget failed: %s", e)
            return 0

    def auto_capture(self, user_message: str) -> int:
        """Cheaply learn obvious durable facts from a user message. No LLM."""
        if not self.enabled or not self._conn:
            return 0
        try:
            msg = (user_message or "").strip()
            if not msg or len(msg) > 300:
                return 0
            saved = 0
            # name -- ONLY when the user DECLARES it, never when they ASK for it.
            # "mera naam kya tha?" / "what was my name" are QUESTIONS, so we must
            # not store "kya tha" as the name. Guard against that two ways:
            #   (a) bail out if the message is clearly a name question, and
            #   (b) treat question words as stop-tokens so they can't leak in.
            _is_name_question = bool(
                re.search(r"\b(?:naam|name)\b", msg, re.IGNORECASE)
                and re.search(
                    r"\b(?:kya|kyaa|kaun|kon|kaunsa|what|whats|what's|who|whos|who's)\b",
                    msg, re.IGNORECASE,
                )
            )
            m = None if _is_name_question else re.search(
                r"\b(?:my name is|i am called|call me|mera naam)\s+"
                r"([A-Za-z][A-Za-z.'-]+(?:\s+[A-Za-z][A-Za-z.'-]+){0,2})",
                msg, re.IGNORECASE,
            )
            if m:
                _stopwords = {"and", "but", "aur", "remember", "yaad", "is",
                              "hai", "hu", "hoon", "i", "who", "that", "ki", "kyunki",
                              # question / past-tense words: never part of a name
                              "kya", "kyaa", "kaun", "kon", "kaunsa", "kahan",
                              "kaisa", "kaise", "kab", "tha", "thi", "the",
                              "what", "whats", "whos"}
                _name_tokens = []
                for _t in m.group(1).split():
                    if _t.lower() in _stopwords:
                        break
                    _name_tokens.append(_t)
                    if len(_name_tokens) >= 2:
                        break
                name = " ".join(_name_tokens).strip(" .,")
                if name:
                    self.remember(name, category="user", key="name", source="auto")
                    saved += 1
            # explicit remember
            m = re.search(
                r"(?:remember that|remember|please remember|yaad rakho(?: ki)?|yaad rakhna(?: ki)?|note that)\s+(.+)",
                msg, re.IGNORECASE,
            )
            if m:
                fact = m.group(1).strip(" .")
                if 2 < len(fact) <= 200:
                    self.remember(fact, category="user", source="auto")
                    saved += 1
            # simple preference
            m = re.search(r"\bi (?:like|love|prefer)\s+(.+)", msg, re.IGNORECASE)
            if m:
                pref = m.group(1).strip(" .")
                if 1 < len(pref) <= 120:
                    self.remember("likes " + pref, category="preference", source="auto")
                    saved += 1
            if saved:
                logger.info("[MEMORY] auto-captured %d fact(s) from message.", saved)
            return saved
        except Exception as e:  # noqa: BLE001
            logger.debug("[MEMORY] auto_capture failed: %s", e)
            return 0

    # ------------------------------------------------------------------ #
    # reads / recall
    # ------------------------------------------------------------------ #
    def recall(self, query, limit: int = 6) -> str:
        if not self.enabled or not self._conn:
            return ""
        try:
            q = str(query or "").strip()
            if not q:
                return ""
            rows = []
            with self._lock:
                c = self._conn
                if self._fts:
                    toks = re.findall(r"[A-Za-z0-9]+", q.lower())
                    if toks:
                        match = " OR ".join(toks)
                        try:
                            cur = c.execute(
                                "SELECT text FROM mem_fts WHERE mem_fts MATCH ? LIMIT ?",
                                (match, limit),
                            )
                            rows = [r[0] for r in cur.fetchall()]
                        except Exception:  # noqa: BLE001 - bad MATCH -> LIKE fallback
                            rows = []
                if not rows:
                    like = f"%{q}%"
                    cur = c.execute(
                        "SELECT value FROM facts WHERE value LIKE ? OR key LIKE ? "
                        "ORDER BY updated_at DESC LIMIT ?",
                        (like, like, limit),
                    )
                    rows = [r[0] for r in cur.fetchall()]
            seen = [r for r in dict.fromkeys(rows) if r]
            if not seen:
                return ""
            return "\n".join("- " + str(r) for r in seen[:limit])
        except Exception as e:  # noqa: BLE001
            logger.debug("[MEMORY] recall failed: %s", e)
            return ""

    def _read_md(self, path, cap: int = 700) -> str:
        try:
            if not path.exists():
                return ""
            raw = path.read_text(encoding="utf-8", errors="ignore").strip()
            if raw.startswith("---"):
                end = raw.find("---", 3)
                if end != -1:
                    raw = raw[end + 3:].strip()
            if len(raw) > cap:
                raw = raw[:cap].rstrip() + " ..."
            return raw
        except Exception:  # noqa: BLE001
            return ""

    def _top_facts(self):
        try:
            with self._lock:
                cur = self._conn.execute(
                    "SELECT key, value FROM facts "
                    "WHERE category IN ('user','preference','project','feedback') "
                    "ORDER BY updated_at DESC LIMIT ?",
                    (MEMORY_MAX_FACTS_INJECT,),
                )
                return cur.fetchall()
        except Exception:  # noqa: BLE001
            return []

    def _recent_corrections(self, limit: int = 3):
        try:
            with self._lock:
                cur = self._conn.execute(
                    "SELECT wrong, right FROM corrections ORDER BY id DESC LIMIT ?", (limit,)
                )
                return cur.fetchall()
        except Exception:  # noqa: BLE001
            return []

    def _last_action(self):
        try:
            with self._lock:
                cur = self._conn.execute(
                    "SELECT tool, target, ok FROM actions ORDER BY id DESC LIMIT 1"
                )
                return cur.fetchone()
        except Exception:  # noqa: BLE001
            return None

    def get_prompt_context(self, query=None) -> str:
        """Small always-injected memory block for the system prompt. Fail-soft."""
        if not self.enabled or not self._conn:
            return ""
        try:
            out = []
            profile = self._read_md(USER_PROFILE_PATH)
            persona = self._read_md(JARVIS_PERSONA_PATH)
            facts = self._top_facts()
            corrections = self._recent_corrections()
            last = self._last_action()
            extra = self.recall(query, limit=4) if query else ""

            if not any([profile, persona, facts, corrections, last, extra]):
                return ""

            out.append(
                "=== MEMORY (long-term, about the user \u2014 use naturally, never reveal the source) ==="
            )
            if persona:
                out.append("[Assistant notes]")
                out.append(persona)
            if profile:
                out.append("[About the user]")
                out.append(profile)
            if facts:
                out.append("[Remembered facts]")
                for k, v in facts:
                    out.append(f"- {k}: {v}" if k else f"- {v}")
            if corrections:
                out.append("[Recent corrections \u2014 do NOT repeat these mistakes]")
                for wrong, right in corrections:
                    if wrong:
                        out.append(f"- Wanted '{right}', not '{wrong}'.")
                    else:
                        out.append(f"- Remember: {right}.")
            if last:
                tool, target, ok = last
                line = f"[Last action] {tool}" + (f" on {target}" if target else "")
                if not ok:
                    line += " (failed)"
                out.append(line)
            if extra:
                out.append("[Possibly relevant memories]")
                out.append(extra)

            block = "\n".join(out)
            if len(block) > MEMORY_CONTEXT_MAX_CHARS:
                block = block[:MEMORY_CONTEXT_MAX_CHARS].rstrip() + " ..."
            return block
        except Exception as e:  # noqa: BLE001
            logger.debug("[MEMORY] get_prompt_context failed: %s", e)
            return ""


class _NullMemory:
    """Fallback used only if MemoryService cannot be constructed at all."""

    enabled = False

    def remember(self, *a, **k):
        return "My long-term memory isn't available right now."

    def record_action(self, *a, **k):
        return None

    def record_correction(self, *a, **k):
        return ""

    def forget(self, *a, **k):
        return 0

    def auto_capture(self, *a, **k):
        return 0

    def recall(self, *a, **k):
        return ""

    def get_prompt_context(self, *a, **k):
        return ""

    def status(self):
        return "unavailable"


_memory_singleton = None
_singleton_lock = threading.Lock()


def get_memory():
    """Return the process-wide MemoryService singleton (never raises)."""
    global _memory_singleton
    if _memory_singleton is not None:
        return _memory_singleton
    with _singleton_lock:
        if _memory_singleton is None:
            try:
                _memory_singleton = MemoryService()
            except Exception as e:  # noqa: BLE001
                logger.warning("[MEMORY] could not construct MemoryService: %s", e)
                _memory_singleton = _NullMemory()
    return _memory_singleton


def augment_system_prompt(system_message: str, query=None) -> str:
    """Append the memory block to a system prompt. Always returns a string."""
    try:
        block = get_memory().get_prompt_context(query)
        if block:
            return system_message + "\n\n" + block
    except Exception as e:  # noqa: BLE001
        logger.debug("[MEMORY] augment_system_prompt failed: %s", e)
    return system_message
