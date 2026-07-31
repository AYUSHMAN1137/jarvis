"""Phase 7 -- Proactive engine.

Listens to system-state events (published by the watcher via the Phase 4 event
bus), and -- using learned habits + an explicit consent registry -- decides
whether to *suggest* a next action to the user.

Key safety rules (reliability #1):
  * SUGGEST-ONLY by default. PROACTIVE_AUTO_ACT=False means JARVIS never runs
    anything on its own; it only surfaces a suggestion the user can accept.
  * A suggestion is only created for an action whose consent mode is not
    "deny"; auto-acting additionally requires mode == "allow" AND the global
    auto-act flag.
  * Nothing is hardcoded: suggestions come from habits learned at runtime
    (injected habit_provider, fed by Phase 8). No habits -> no suggestions.
  * Rate-limited (PROACTIVE_MIN_INTERVAL_SECONDS) so it can never nag.
  * Everything is fail-soft; the engine must never crash the watcher/bus.

Dependency-injectable (bus, habit_provider, db_path, clock) for unit testing.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from collections import deque
from typing import Any, Callable, Dict, List, Optional

import config as _cfg
from app.services.agent.proactive.events import (
    EVT_APP_OPENED, EVT_WINDOW_FOCUSED, EVT_CLIPBOARD_CHANGED,
    EVT_SETTINGS_CHANGED,
)

logger = logging.getLogger("J.A.R.V.I.S")

_ENABLED = bool(getattr(_cfg, "PHASE7_ENABLED", True))
_AUTO_ACT = bool(getattr(_cfg, "PROACTIVE_AUTO_ACT", False))
_MIN_INTERVAL = float(getattr(_cfg, "PROACTIVE_MIN_INTERVAL_SECONDS", 45))
_DB_PATH = getattr(_cfg, "PROACTIVE_DB_PATH", None)

CONSENT_ASK = "ask"
CONSENT_ALLOW = "allow"
CONSENT_DENY = "deny"
VALID_CONSENT = {CONSENT_ASK, CONSENT_ALLOW, CONSENT_DENY}

_TRIGGER_EVENTS = (
    EVT_APP_OPENED, EVT_WINDOW_FOCUSED, EVT_CLIPBOARD_CHANGED, EVT_SETTINGS_CHANGED,
)


class ProactiveEngine:
    def __init__(
        self,
        bus: Any = None,
        habit_provider: Optional[Callable[[str, dict], List[dict]]] = None,
        db_path: Optional[str] = None,
        clock: Optional[Callable[[], float]] = None,
        auto_act: Optional[bool] = None,
        min_interval: Optional[float] = None,
    ) -> None:
        self.enabled = _ENABLED
        self.started = False
        self.auto_act = _AUTO_ACT if auto_act is None else bool(auto_act)
        self.min_interval = _MIN_INTERVAL if min_interval is None else float(min_interval)
        self._bus_override = bus
        self.bus = None
        self._habit_provider = habit_provider
        self._clock = clock or time.time
        self._db_path = db_path or _DB_PATH
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()
        self._recent: deque = deque(maxlen=60)
        self._last_suggest_at = 0.0
        self._counters = {"events": 0, "suggested": 0, "suppressed": 0,
                          "accepted": 0, "dismissed": 0, "auto_acted": 0}
        # Section 12: event deduplication tracking
        self._last_event_times: Dict[str, float] = {}
        self._context_cooldowns: Dict[str, float] = {}

    def start(self) -> None:
        if not self.enabled:
            logger.info("[PROACTIVE] Phase 7 disabled (PHASE7_ENABLED=False).")
            return
        if self.started:
            return
        try:
            self._init_db()
            try:
                if self._bus_override is not None:
                    self.bus = self._bus_override
                else:
                    from app.services.agent.checker.event_bus import get_event_bus
                    self.bus = get_event_bus()
                if self.bus is not None:
                    for et in _TRIGGER_EVENTS:
                        self.bus.subscribe(et, self._make_handler(et))
            except Exception as e:  # noqa: BLE001
                logger.warning("[PROACTIVE] bus wiring failed (non-fatal): %s", e)
                self.bus = None
            self.started = True
            logger.info(
                "[PROACTIVE] Phase 7 online -- suggest-only=%s, interval=%.0fs.",
                (not self.auto_act), self.min_interval,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[PROACTIVE] Phase 7 failed to start (non-fatal): %s", e)
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
            return
        from app.services.db import open_db
        self._conn = open_db(self._db_path, label="proactive")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS proactive_suggestions ("
            "id TEXT PRIMARY KEY, created_at REAL, trigger_kind TEXT, "
            "trigger_detail TEXT, action TEXT, text TEXT, command TEXT, "
            "status TEXT DEFAULT 'pending', resolved_at REAL)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS proactive_consent ("
            "action TEXT PRIMARY KEY, mode TEXT, updated_at REAL)"
        )
        self._conn.commit()

    def get_consent(self, action: str) -> str:
        action = (action or "").strip().lower()
        if not action or self._conn is None:
            return CONSENT_ASK
        try:
            row = self._conn.execute(
                "SELECT mode FROM proactive_consent WHERE action=?", (action,)
            ).fetchone()
            if row and row[0] in VALID_CONSENT:
                return row[0]
        except Exception:  # noqa: BLE001
            pass
        return CONSENT_ASK

    def set_consent(self, action: str, mode: str) -> bool:
        action = (action or "").strip().lower()
        if not action or mode not in VALID_CONSENT or self._conn is None:
            return False
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO proactive_consent(action, mode, updated_at) "
                    "VALUES(?,?,?) ON CONFLICT(action) DO UPDATE SET "
                    "mode=excluded.mode, updated_at=excluded.updated_at",
                    (action, mode, self._clock()),
                )
                self._conn.commit()
            return True
        except Exception as e:  # noqa: BLE001
            logger.debug("[PROACTIVE] set_consent failed: %s", e)
            return False

    def set_habit_provider(self, provider: Optional[Callable[[str, dict], List[dict]]]) -> None:
        """Inject (or replace) the habit source. Phase 8 wires its UserModel
        here at startup so suggestions are driven by learned habits."""
        self._habit_provider = provider

    def _make_handler(self, event_type: str):
        def _handler(payload):
            self.on_event(event_type, payload or {})
        return _handler

    def on_event(self, event_type: str, payload: dict) -> Optional[dict]:
        try:
            self._bump("events")
            if event_type not in _TRIGGER_EVENTS:
                return None
            context = self._context_key(event_type, payload)
            if not context:
                return None
            now = self._clock()
            # Section 12.2: dedup events by type + state fingerprint + time window
            dedup_key = f"{event_type}:{context}"
            dedup_window = self.min_interval / 3  # deduplicate rapid same-state events
            last_event = self._last_event_times.get(dedup_key, 0.0)
            if (now - last_event) < dedup_window:
                self._bump("suppressed")
                return None
            self._last_event_times[dedup_key] = now
            # Section 12.3: per-context cooldown
            context_cooldown_key = f"ctx:{context}"
            last_context = self._context_cooldowns.get(context_cooldown_key, 0.0)
            if (now - last_context) < self.min_interval:
                self._bump("suppressed")
                return None
            # Global rate limit
            if (now - self._last_suggest_at) < self.min_interval:
                self._bump("suppressed")
                return None
            habits = self._habits(context, payload)
            if not habits:
                return None
            best = habits[0]
            action = (best.get("action") or best.get("tool") or "").strip()
            if not action:
                return None
            # Section 12.4: minimum confidence check
            confidence = float(best.get("confidence", 0))
            if confidence < 0.3:
                self._bump("suppressed")
                return None
            mode = self.get_consent(action)
            if mode == CONSENT_DENY:
                self._bump("suppressed")
                return None
            if self._has_pending(context, action):
                self._bump("suppressed")
                return None
            text = best.get("text") or ("Shall I %s?" % action.replace("_", " "))
            command = best.get("command") or {"tool": action, "args": best.get("args") or {}}
            sug = self._create_suggestion(event_type, context, action, text, command)
            self._last_suggest_at = now
            self._context_cooldowns[context_cooldown_key] = now
            self._bump("suggested")
            if self.auto_act and mode == CONSENT_ALLOW:
                sug["auto"] = True
                sug["reason"] = f"Habit: {confidence:.0%} confidence after {best.get('count', 0)} observations"
                self._bump("auto_acted")
            # Section 12.5: add reason for all suggestions
            if "reason" not in sug:
                sug["reason"] = f"You usually do this ({best.get('count', 0)} times observed)"
            return sug
        except Exception as e:  # noqa: BLE001
            logger.debug("[PROACTIVE] on_event failed: %s", e)
            return None

    def _has_pending(self, context: str, action: str) -> bool:
        if self._conn is None:
            return False
        try:
            return bool(self._conn.execute(
                "SELECT 1 FROM proactive_suggestions WHERE trigger_detail=? "
                "AND action=? AND status='pending' LIMIT 1", (context, action)
            ).fetchone())
        except Exception:
            return False

    @staticmethod
    def _context_key(event_type: str, payload: dict) -> str:
        if event_type in (EVT_APP_OPENED, EVT_WINDOW_FOCUSED):
            return (payload.get("title") or "").strip().lower()
        if event_type == EVT_SETTINGS_CHANGED:
            return ("setting:" + str(payload.get("key") or "")).strip().lower()
        if event_type == EVT_CLIPBOARD_CHANGED:
            return "clipboard"
        return ""

    def _habits(self, context: str, payload: dict) -> List[dict]:
        if self._habit_provider is None:
            return []
        try:
            out = self._habit_provider(context, payload) or []
            return [h for h in out if isinstance(h, dict)]
        except Exception as e:  # noqa: BLE001
            logger.debug("[PROACTIVE] habit_provider failed: %s", e)
            return []

    def _create_suggestion(self, kind, context, action, text, command) -> dict:
        sug = {
            "id": uuid.uuid4().hex[:12],
            "created_at": self._clock(),
            "trigger_kind": kind,
            "trigger_detail": context,
            "action": action,
            "text": text,
            "command": command,
            "status": "pending",
        }
        if self._conn is not None:
            try:
                with self._lock:
                    self._conn.execute(
                        "INSERT INTO proactive_suggestions "
                        "(id, created_at, trigger_kind, trigger_detail, action, "
                        "text, command, status) VALUES(?,?,?,?,?,?,?, 'pending')",
                        (sug["id"], sug["created_at"], kind, context, action,
                         text, json.dumps(command)),
                    )
                    self._conn.commit()
            except Exception as e:  # noqa: BLE001
                logger.debug("[PROACTIVE] persist suggestion failed: %s", e)
        self._note("suggest", action, text)
        return sug

    def get_pending(self, limit: int = 10) -> List[dict]:
        if self._conn is None:
            return []
        try:
            rows = self._conn.execute(
                "SELECT id, created_at, trigger_kind, trigger_detail, action, "
                "text, command, status FROM proactive_suggestions "
                "WHERE status='pending' ORDER BY created_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]
        except Exception:  # noqa: BLE001
            return []

    def accept(self, suggestion_id: str) -> Optional[dict]:
        sug = self._resolve(suggestion_id, "accepted")
        if sug:
            self._bump("accepted")
            self._note("accept", sug.get("action", ""), sug.get("text", ""))
        return sug

    def dismiss(self, suggestion_id: str) -> bool:
        sug = self._resolve(suggestion_id, "dismissed")
        if sug:
            self._bump("dismissed")
            self._note("dismiss", sug.get("action", ""), sug.get("text", ""))
        return bool(sug)

    def _resolve(self, suggestion_id: str, status: str) -> Optional[dict]:
        if self._conn is None or not suggestion_id:
            return None
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT id, created_at, trigger_kind, trigger_detail, action, "
                    "text, command, status FROM proactive_suggestions "
                    "WHERE id=? AND status='pending'",
                    (suggestion_id,),
                ).fetchone()
                if not row:
                    return None
                self._conn.execute(
                    "UPDATE proactive_suggestions SET status=?, resolved_at=? WHERE id=?",
                    (status, self._clock(), suggestion_id),
                )
                self._conn.commit()
            return self._row_to_dict(row)
        except Exception as e:  # noqa: BLE001
            logger.debug("[PROACTIVE] resolve failed: %s", e)
            return None

    @staticmethod
    def _row_to_dict(r) -> dict:
        try:
            command = json.loads(r[6]) if r[6] else {}
        except Exception:  # noqa: BLE001
            command = {}
        return {
            "id": r[0], "created_at": r[1], "trigger_kind": r[2],
            "trigger_detail": r[3], "action": r[4], "text": r[5],
            "command": command, "status": r[7],
        }

    def _bump(self, key: str) -> None:
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + 1

    def _note(self, action_kind: str, action: str, text: str = "") -> None:
        try:
            self._recent.append({
                "time": self._clock(), "kind": "proactive",
                "action": action_kind, "target": action, "text": text,
            })
        except Exception:  # noqa: BLE001
            pass

    def recent_activity(self, limit: int = 30) -> List[dict]:
        try:
            return list(reversed(list(self._recent)[-int(limit):]))
        except Exception:  # noqa: BLE001
            return []

    def health(self) -> dict:
        return {
            "enabled": self.enabled, "started": self.started,
            "auto_act": self.auto_act, "suggest_only": not self.auto_act,
            "bus": self.bus is not None, "db": self._conn is not None,
            "min_interval": self.min_interval,
        }

    def stats(self) -> dict:
        out = {"enabled": self.enabled, "started": self.started,
               "auto_act": self.auto_act}
        with self._lock:
            out.update(self._counters)
        return out


_engine: Optional[ProactiveEngine] = None
_engine_lock = threading.Lock()


def get_phase7() -> ProactiveEngine:
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = ProactiveEngine()
    return _engine
