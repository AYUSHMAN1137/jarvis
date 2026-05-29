import threading
import time
import json
import logging
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from config import GROQ_API_KEYS, SERPER_API_KEYS

logger = logging.getLogger("J.A.R.V.I.S")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mask_api_key(key: str) -> str:
    if not key or len(key) <= 12:
        return "***masked***"
    return f"{key[:8]}...{key[-4:]}"


class ApiKeyMonitor:
    def __init__(self):
        self._lock = threading.Lock()
        self._started_at = _utc_now_iso()
        self._events = deque(maxlen=300)
        self._state_path = Path(__file__).resolve().parents[2] / "database" / "monitor_state.json"
        self._persist_interval_seconds = 2.0
        self._last_persist_at = 0.0

        self._groq: Dict[int, Dict[str, Any]] = {}
        for idx, key in enumerate(GROQ_API_KEYS):
            self._groq[idx] = self._new_groq_stat(idx, key)

        self._providers: Dict[str, Dict[str, Any]] = {
            "serper": {
                "configured": bool(SERPER_API_KEYS),
                "key_count": len(SERPER_API_KEYS),
                "attempts": 0,
                "successes": 0,
                "failures": 0,
                "last_success_at": None,
                "last_error": "",
            }
        }
        self._load_persisted_state()

    @staticmethod
    def _new_groq_stat(idx: int, key: str) -> Dict[str, Any]:
        return {
            "key_index": idx,
            "key_label": f"GROQ_API_KEY_{idx + 1}" if idx > 0 else "GROQ_API_KEY",
            "key_masked": _mask_api_key(key),
            "attempts": 0,
            "successes": 0,
            "failures": 0,
            "rate_limits": 0,
            "in_flight": 0,
            "last_used_at": None,
            "last_success_at": None,
            "last_error": "",
            "last_error_at": None,
            "last_latency_ms": None,
        }

    def _ensure_key_stat(self, idx: int):
        if idx in self._groq:
            return
        key = GROQ_API_KEYS[idx] if 0 <= idx < len(GROQ_API_KEYS) else ""
        self._groq[idx] = self._new_groq_stat(idx, key)

    def _event(self, event_type: str, provider: str, details: Dict[str, Any]):
        self._events.appendleft(
            {
                "timestamp": _utc_now_iso(),
                "event": event_type,
                "provider": provider,
                **details,
            }
        )

    def _serialize_persisted_state(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "saved_at": _utc_now_iso(),
            "groq": {
                "keys": {
                    str(idx): {
                        "attempts": int(stat.get("attempts", 0)),
                        "successes": int(stat.get("successes", 0)),
                        "failures": int(stat.get("failures", 0)),
                        "rate_limits": int(stat.get("rate_limits", 0)),
                        "last_success_at": stat.get("last_success_at"),
                        "last_error": stat.get("last_error", ""),
                        "last_error_at": stat.get("last_error_at"),
                    }
                    for idx, stat in self._groq.items()
                }
            },
            "providers": {
                name: {
                    "configured": bool(p.get("configured", False)),
                    "attempts": int(p.get("attempts", 0)),
                    "successes": int(p.get("successes", 0)),
                    "failures": int(p.get("failures", 0)),
                    "last_success_at": p.get("last_success_at"),
                    "last_error": p.get("last_error", ""),
                }
                for name, p in self._providers.items()
            },
        }

    def _write_state_locked(self):
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._serialize_persisted_state(), ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._state_path)
            self._last_persist_at = time.perf_counter()
        except Exception as e:
            logger.warning("[MONITOR] Failed to persist monitor state: %s", e)

    def _persist_if_due_locked(self, force: bool = False):
        now = time.perf_counter()
        if not force and (now - self._last_persist_at) < self._persist_interval_seconds:
            return
        self._write_state_locked()

    def _load_persisted_state(self):
        try:
            if not self._state_path.exists():
                return
            raw = self._state_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            keys = ((data.get("groq") or {}).get("keys") or {})
            for idx_str, val in keys.items():
                try:
                    idx = int(idx_str)
                except Exception:
                    continue
                self._ensure_key_stat(idx)
                stat = self._groq[idx]
                stat["attempts"] = int(val.get("attempts", stat["attempts"]))
                stat["successes"] = int(val.get("successes", stat["successes"]))
                stat["failures"] = int(val.get("failures", stat["failures"]))
                stat["rate_limits"] = int(val.get("rate_limits", stat["rate_limits"]))
                stat["last_success_at"] = val.get("last_success_at") or stat.get("last_success_at")
                stat["last_error"] = val.get("last_error", stat.get("last_error", ""))
                stat["last_error_at"] = val.get("last_error_at") or stat.get("last_error_at")

            providers = data.get("providers") or {}
            for name, pval in providers.items():
                p = self._providers.setdefault(
                    name,
                    {"configured": True, "attempts": 0, "successes": 0, "failures": 0, "last_success_at": None, "last_error": ""},
                )
                p["attempts"] = int(pval.get("attempts", p["attempts"]))
                p["successes"] = int(pval.get("successes", p["successes"]))
                p["failures"] = int(pval.get("failures", p["failures"]))
                p["last_success_at"] = pval.get("last_success_at") or p.get("last_success_at")
                p["last_error"] = pval.get("last_error", p.get("last_error", ""))
        except Exception as e:
            logger.warning("[MONITOR] Failed to load persisted monitor state: %s", e)

    def record_groq_attempt(self, key_index: int, operation: str, source: str, trace_id: Optional[str] = None):
        with self._lock:
            self._ensure_key_stat(key_index)
            stat = self._groq[key_index]
            stat["attempts"] += 1
            stat["in_flight"] += 1
            stat["last_used_at"] = _utc_now_iso()
            self._event(
                "attempt",
                "groq",
                {
                    "key_index": key_index,
                    "key_label": stat["key_label"],
                    "operation": operation,
                    "source": source,
                    "trace_id": trace_id,
                },
            )
            self._persist_if_due_locked()

    def record_groq_success(
        self,
        key_index: int,
        operation: str,
        source: str,
        latency_ms: Optional[int] = None,
        trace_id: Optional[str] = None,
    ):
        with self._lock:
            self._ensure_key_stat(key_index)
            stat = self._groq[key_index]
            stat["successes"] += 1
            stat["in_flight"] = max(0, stat["in_flight"] - 1)
            stat["last_success_at"] = _utc_now_iso()
            if latency_ms is not None:
                stat["last_latency_ms"] = int(latency_ms)
            self._event(
                "success",
                "groq",
                {
                    "key_index": key_index,
                    "key_label": stat["key_label"],
                    "operation": operation,
                    "source": source,
                    "latency_ms": latency_ms,
                    "trace_id": trace_id,
                },
            )
            self._persist_if_due_locked()

    def record_groq_failure(
        self,
        key_index: int,
        operation: str,
        source: str,
        error: str,
        is_rate_limit: bool = False,
        latency_ms: Optional[int] = None,
        trace_id: Optional[str] = None,
    ):
        with self._lock:
            self._ensure_key_stat(key_index)
            stat = self._groq[key_index]
            stat["failures"] += 1
            if is_rate_limit:
                stat["rate_limits"] += 1
            stat["in_flight"] = max(0, stat["in_flight"] - 1)
            stat["last_error"] = (error or "")[:240]
            stat["last_error_at"] = _utc_now_iso()
            if latency_ms is not None:
                stat["last_latency_ms"] = int(latency_ms)
            self._event(
                "failure",
                "groq",
                {
                    "key_index": key_index,
                    "key_label": stat["key_label"],
                    "operation": operation,
                    "source": source,
                    "rate_limited": bool(is_rate_limit),
                    "latency_ms": latency_ms,
                    "error": (error or "")[:120],
                    "trace_id": trace_id,
                },
            )
            self._persist_if_due_locked()

    def record_provider_attempt(self, provider: str, operation: str, source: str, trace_id: Optional[str] = None):
        with self._lock:
            p = self._providers.setdefault(
                provider,
                {"configured": True, "attempts": 0, "successes": 0, "failures": 0, "last_success_at": None, "last_error": ""},
            )
            p["attempts"] += 1
            self._event("attempt", provider, {"operation": operation, "source": source, "trace_id": trace_id})
            self._persist_if_due_locked()

    def record_provider_success(self, provider: str, operation: str, source: str, trace_id: Optional[str] = None):
        with self._lock:
            p = self._providers.setdefault(
                provider,
                {"configured": True, "attempts": 0, "successes": 0, "failures": 0, "last_success_at": None, "last_error": ""},
            )
            p["successes"] += 1
            p["last_success_at"] = _utc_now_iso()
            self._event("success", provider, {"operation": operation, "source": source, "trace_id": trace_id})
            self._persist_if_due_locked()

    def record_provider_failure(
        self, provider: str, operation: str, source: str, error: str, trace_id: Optional[str] = None
    ):
        with self._lock:
            p = self._providers.setdefault(
                provider,
                {"configured": True, "attempts": 0, "successes": 0, "failures": 0, "last_success_at": None, "last_error": ""},
            )
            p["failures"] += 1
            p["last_error"] = (error or "")[:240]
            self._event(
                "failure",
                provider,
                {"operation": operation, "source": source, "error": (error or "")[:120], "trace_id": trace_id},
            )
            self._persist_if_due_locked()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            keys = [self._groq[idx].copy() for idx in sorted(self._groq.keys())]
            total_attempts = sum(k["attempts"] for k in keys)
            total_successes = sum(k["successes"] for k in keys)
            total_failures = sum(k["failures"] for k in keys)
            total_rate_limits = sum(k["rate_limits"] for k in keys)
            in_flight = sum(k["in_flight"] for k in keys)
            uptime_s = int(max(0.0, time.time() - datetime.fromisoformat(self._started_at).timestamp()))

            return {
                "timestamp": _utc_now_iso(),
                "started_at": self._started_at,
                "uptime_seconds": uptime_s,
                "groq": {
                    "configured_keys": len(GROQ_API_KEYS),
                    "summary": {
                        "attempts": total_attempts,
                        "successes": total_successes,
                        "failures": total_failures,
                        "rate_limits": total_rate_limits,
                        "in_flight": in_flight,
                    },
                    "keys": keys,
                },
                "providers": {name: data.copy() for name, data in self._providers.items()},
                "events": list(self._events)[:120],
            }


_MONITOR = ApiKeyMonitor()


def get_api_key_monitor() -> ApiKeyMonitor:
    return _MONITOR
