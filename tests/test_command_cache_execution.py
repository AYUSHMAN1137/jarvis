"""Regression tests for execution-level command-cache promotion."""

from __future__ import annotations

import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path


# Keep this unit test independent of optional runtime dependencies.
_tmp = tempfile.TemporaryDirectory()
_config = types.ModuleType("config")
_config.BASE_DIR = Path(_tmp.name)
_config.PHASE6_ENABLED = True
_config.COMMAND_CACHE_DB_PATH = Path(_tmp.name) / "cache.db"
_config.CACHE_MAX_ENTRIES = 100
sys.modules.setdefault("config", _config)

coordinator_module = importlib.import_module("app.services.agent.cache.coordinator")
Phase6Coordinator = coordinator_module.Phase6Coordinator


class FakeCache:
    enabled = True

    def __init__(self):
        self.rows = {}

    @staticmethod
    def normalize(value):
        return " ".join(str(value or "").strip().lower().split()).strip(" .!?,;:\"'")

    def put(self, trigger, kind, payload, verified=True):
        self.rows[self.normalize(trigger)] = {
            "kind": kind,
            "payload": payload,
            "verified": verified,
        }
        return True

    def get(self, trigger):
        return self.rows.get(self.normalize(trigger))

    def evict(self, trigger):
        self.rows.pop(self.normalize(trigger), None)

    def record_hit(self, trigger):
        return None

    def stats(self):
        return {"active": len(self.rows)}

    def list_entries(self, limit=50):
        return list(self.rows.values())[:limit]


class FakeBus:
    def __init__(self):
        self.handlers = {}

    def subscribe(self, event_type, handler):
        self.handlers.setdefault(event_type, []).append(handler)


class ExecutionCacheTests(unittest.TestCase):
    def setUp(self):
        self.cache = FakeCache()
        self.bus = FakeBus()
        self.coord = Phase6Coordinator(
            cache=self.cache,
            bus=self.bus,
            is_dangerous=lambda _tool: False,
            is_referential=lambda _text: False,
        )
        self.coord.start()

    @staticmethod
    def completed(execution_id, command, steps, ok=True):
        return {
            "execution_id": execution_id,
            "user_message": command,
            "steps": steps,
            "ok": ok,
        }

    @staticmethod
    def verdict(execution_id, action_id, command, verdict="PASS"):
        return {
            "execution_id": execution_id,
            "action_id": action_id,
            "user_message": command,
            "verdict": verdict,
            "ok": verdict != "FAIL",
        }

    def test_multistep_waits_for_every_verdict_then_stores_complete_plan(self):
        steps = [
            {"action_id": "a1", "tool": "open_application", "args": {"app_name": "notepad"}},
            {"action_id": "a2", "tool": "type_text", "args": {"text": "hello"}},
        ]
        self.coord._on_execution_completed(self.completed("run-1", "open and type", steps))
        self.coord._on_verified(self.verdict("run-1", "a1", "open and type"))
        self.assertNotIn("open and type", self.cache.rows)
        self.coord._on_verified(self.verdict("run-1", "a2", "open and type"))
        row = self.cache.rows["open and type"]
        self.assertEqual("plan", row["kind"])
        self.assertEqual(2, len(row["payload"]["steps"]))

    def test_out_of_order_verdicts_are_joined(self):
        steps = [{"action_id": "a1", "tool": "open_application", "args": {}}]
        self.coord._on_verified(self.verdict("run-2", "a1", "open app"))
        self.assertNotIn("open app", self.cache.rows)
        self.coord._on_execution_completed(self.completed("run-2", "open app", steps))
        self.assertEqual("tool", self.cache.rows["open app"]["kind"])

    def test_unknown_verdict_never_promotes(self):
        steps = [{"action_id": "a1", "tool": "type_text", "args": {}}]
        self.coord._on_execution_completed(self.completed("run-3", "type it", steps))
        self.coord._on_verified(self.verdict("run-3", "a1", "type it", "UNKNOWN"))
        self.assertNotIn("type it", self.cache.rows)

    def test_failure_evicts_existing_entry(self):
        self.cache.put("open app", "tool", {"tool": "open_application", "args": {}})
        steps = [{"action_id": "a1", "tool": "open_application", "args": {}}]
        self.coord._on_execution_completed(self.completed("run-4", "open app", steps))
        self.coord._on_verified(self.verdict("run-4", "a1", "open app", "FAIL"))
        self.assertNotIn("open app", self.cache.rows)

    def test_incomplete_execution_never_promotes(self):
        steps = [{"action_id": "a1", "tool": "open_application", "args": {}}]
        self.coord._on_verified(self.verdict("run-5", "a1", "open app"))
        self.coord._on_execution_completed(self.completed("run-5", "open app", steps, ok=False))
        self.assertNotIn("open app", self.cache.rows)


if __name__ == "__main__":
    unittest.main()
