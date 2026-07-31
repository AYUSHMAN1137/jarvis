"""M13 §4.4 -- speed is EARNED, and only by commands that stand on their own.

A cache hit EXECUTES something. So a phrasing may only be promoted when both
hold:

  1. every action of the run verified PASS (already true before M13), and
  2. the ORIGINAL utterance carried its own meaning.

Condition 2 is the new one. "close it" can succeed a hundred times and must
still never be promoted: replaying it tomorrow would close whatever happens to
be open then, which is not what was verified.
"""

from __future__ import annotations

import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path

_tmp = tempfile.TemporaryDirectory()
_config = types.ModuleType("config")
_config.BASE_DIR = Path(_tmp.name)
_config.PHASE6_ENABLED = True
_config.COMMAND_CACHE_DB_PATH = Path(_tmp.name) / "cache.db"
_config.CACHE_MAX_ENTRIES = 100


def _delegate_to_real_config(name: str):
    real = sys.modules.get("_real_config")
    if real is None:
        spec = importlib.util.spec_from_file_location(
            "_real_config", Path(__file__).resolve().parent.parent / "config.py")
        real = importlib.util.module_from_spec(spec)
        sys.modules["_real_config"] = real
        spec.loader.exec_module(real)
    try:
        return getattr(real, name)
    except AttributeError as exc:
        raise AttributeError(name) from exc


_config.__getattr__ = _delegate_to_real_config
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
        self.rows[self.normalize(trigger)] = {"kind": kind, "payload": payload,
                                              "verified": verified}
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


class SelfContainedPromotionTests(unittest.TestCase):
    def setUp(self):
        self.cache = FakeCache()
        self.coord = Phase6Coordinator(
            cache=self.cache, bus=FakeBus(),
            is_dangerous=lambda _tool: False,
            is_referential=lambda _text: False,   # isolate the new gate
        )
        self.coord.start()

    def _verified_run(self, command, execution_id="run"):
        steps = [{"action_id": "a1", "tool": "open_website",
                  "args": {"target": "youtube"}}]
        self.coord._on_execution_completed({
            "execution_id": execution_id, "user_message": command,
            "steps": steps, "ok": True})
        self.coord._on_verified({
            "execution_id": execution_id, "action_id": "a1",
            "user_message": command, "verdict": "PASS", "ok": True})

    def test_a_self_contained_command_is_promoted(self):
        self.coord.note_eligibility("open youtube", True)
        self._verified_run("open youtube")
        self.assertIn("open youtube", self.cache.rows)

    def test_a_context_dependent_command_is_never_promoted(self):
        self.coord.note_eligibility("close it", False)
        self._verified_run("close it")
        self.assertNotIn("close it", self.cache.rows)

    def test_repeated_success_does_not_earn_a_context_dependent_command_a_place(self):
        for index in range(5):
            self.coord.note_eligibility("play that one", False)
            self._verified_run("play that one", execution_id=f"run-{index}")
        self.assertEqual(self.cache.rows, {})

    def test_an_unrecorded_command_is_not_promoted(self):
        """Not knowing whether a phrasing depends on context is not a licence to
        replay it later."""
        self._verified_run("open youtube")
        self.assertNotIn("open youtube", self.cache.rows)

    def test_the_atomic_verdict_path_is_gated_too(self):
        """The single-action compatibility path must not be a way round the gate."""
        self.coord.note_eligibility("close it", False)
        self.coord._on_verified({
            "user_message": "close it", "verdict": "PASS", "ok": True,
            "steps": [{"tool": "close_application", "args": {}}],
        })
        self.assertNotIn("close it", self.cache.rows)

        self.coord.note_eligibility("close spotify", True)
        self.coord._on_verified({
            "user_message": "close spotify", "verdict": "PASS", "ok": True,
            "steps": [{"tool": "close_application", "args": {"app_name": "spotify"}}],
        })
        self.assertIn("close spotify", self.cache.rows)

    def test_the_flag_can_restore_the_old_behaviour(self):
        import config as cfg
        saved = getattr(cfg, "CACHE_REQUIRE_SELF_CONTAINED", True)
        cfg.CACHE_REQUIRE_SELF_CONTAINED = False
        self.addCleanup(lambda: setattr(cfg, "CACHE_REQUIRE_SELF_CONTAINED", saved))
        self._verified_run("open youtube")
        self.assertIn("open youtube", self.cache.rows)

    def test_eligibility_is_bounded(self):
        for index in range(400):
            self.coord.note_eligibility(f"command number {index}", True)
        self.assertLessEqual(len(self.coord._eligibility), 200)

    def test_the_newest_note_wins(self):
        self.coord.note_eligibility("open youtube", False)
        self.coord.note_eligibility("open youtube", True)
        self._verified_run("open youtube")
        self.assertIn("open youtube", self.cache.rows)

    def test_a_failed_run_still_evicts_regardless_of_eligibility(self):
        self.cache.put("open youtube", "tool",
                       {"tool": "open_website", "args": {"target": "youtube"}})
        steps = [{"action_id": "a1", "tool": "open_website", "args": {}}]
        self.coord._on_execution_completed({
            "execution_id": "run-f", "user_message": "open youtube",
            "steps": steps, "ok": True})
        self.coord._on_verified({
            "execution_id": "run-f", "action_id": "a1",
            "user_message": "open youtube", "verdict": "FAIL", "ok": True})
        self.assertNotIn("open youtube", self.cache.rows)

    def test_normalisation_means_punctuation_does_not_lose_the_note(self):
        self.coord.note_eligibility("Open YouTube.", True)
        self._verified_run("open youtube")
        self.assertIn("open youtube", self.cache.rows)


if __name__ == "__main__":
    unittest.main()
