"""M13 §3.1 -- regression gate for the frontend acknowledgement chain.

Before this, `register_dispatch` existed but was never called from anywhere, so
`acknowledge_dispatch` always popped `None`, `_dispatch_results` was never
written, and `_verify_frontend` returned UNKNOWN for every web tool ever run --
permanently. That is why `command_cache` had almost nothing in it: no web action
could ever verify, so nothing could ever be promoted.

These tests walk the whole chain with a real Phase4Coordinator (no browser, no
network) and assert a PASS is reachable.
"""

import unittest

from app.services.agent import action_sink
from app.services.agent.checker import models
from app.services.agent.checker.checker import Checker
from app.services.agent.checker.coordinator import Phase4Coordinator
from app.services.agent.execution import ExecutionContext, ExecutionCoordinator
from app.services.agent.tool_registry import ToolParam, ToolRegistry, ToolSpec


class NullMemory:
    def record_action(self, *args, **kwargs):
        pass


class RecordingPhase4(Phase4Coordinator):
    """A real coordinator with the event bus left unwired.

    register_dispatch / acknowledge_dispatch / dispatch_result are pure
    bookkeeping and do not need the bus, so this exercises the production code
    without starting any threads.
    """

    def publish_action_done(self, *args, **kwargs):
        pass

    def publish_execution_completed(self, *args, **kwargs):
        pass


def _web_registry():
    reg = ToolRegistry()

    def open_site(url: str = "https://example.com") -> str:
        action_sink.add_open(url)
        return f"Opening {url}."

    reg.register(ToolSpec(
        "open_site", "opens a site", open_site, category="web",
        params=[ToolParam("url", "string", "url to open", required=False)],
        verification={"family": "frontend"}))
    return reg


class FrontendAckChainTests(unittest.TestCase):
    def setUp(self):
        action_sink.reset()
        self.phase4 = RecordingPhase4()
        self.coordinator = ExecutionCoordinator(
            _web_registry(), NullMemory(), self.phase4)

    def _run_one(self, url="https://example.com"):
        context = ExecutionContext(user_message="open example", source="test")
        action = self.coordinator.action("open_site", {"url": url})
        result = self.coordinator.execute_action(
            context, action, confirmation_already_checked=True)
        return action, result

    def test_execute_action_registers_the_dispatch(self):
        action, result = self._run_one()
        meta = result.frontend_actions["_meta"]
        self.assertTrue(meta["dispatch_id"])
        pending = {d["dispatch_id"] for d in self.phase4.pending_dispatches(3600)}
        self.assertIn(meta["dispatch_id"], pending,
                      "the dispatch must be registered inside execute_action, "
                      "before the payload can reach the browser")
        self.assertEqual(meta["action_id"], action.action_id)

    def test_acknowledgement_reaches_dispatch_result(self):
        action, result = self._run_one()
        dispatch_id = result.frontend_actions["_meta"]["dispatch_id"]

        matched = self.phase4.acknowledge_dispatch(
            dispatch_id, attempted=True, accepted=True)
        self.assertTrue(matched, "a registered dispatch must match its ack")

        outcome = self.phase4.dispatch_result(action.action_id)
        self.assertIsNotNone(outcome)
        self.assertTrue(outcome["accepted"])

    def test_verify_frontend_can_return_pass(self):
        """The whole point: a web tool must be able to verify as PASS."""
        action, result = self._run_one()
        dispatch_id = result.frontend_actions["_meta"]["dispatch_id"]
        self.phase4.acknowledge_dispatch(dispatch_id, attempted=True, accepted=True)

        import app.services.agent.checker as pkg
        saved = pkg.get_phase4
        pkg.get_phase4 = lambda: self.phase4
        self.addCleanup(lambda: setattr(pkg, "get_phase4", saved))

        verdict = Checker()._verify_frontend(
            "open_site", {"url": "https://example.com"}, action.action_id)
        self.assertEqual(verdict.verdict, models.PASS)

    def test_a_rejected_dispatch_verifies_as_fail(self):
        action, result = self._run_one()
        dispatch_id = result.frontend_actions["_meta"]["dispatch_id"]
        self.phase4.acknowledge_dispatch(
            dispatch_id, attempted=True, accepted=False, error="popup blocked")

        import app.services.agent.checker as pkg
        saved = pkg.get_phase4
        pkg.get_phase4 = lambda: self.phase4
        self.addCleanup(lambda: setattr(pkg, "get_phase4", saved))

        verdict = Checker()._verify_frontend(
            "open_site", {"url": "https://example.com"}, action.action_id)
        self.assertEqual(verdict.verdict, models.FAIL)
        self.assertIn("popup blocked", verdict.reason)

    def test_unknown_dispatch_id_is_not_matched(self):
        self.assertFalse(self.phase4.acknowledge_dispatch("not-a-real-dispatch"))

    def test_stale_pending_dispatches_are_swept(self):
        """A browser that never answers must not grow the pending dict forever."""
        import config as _cfg
        saved = getattr(_cfg, "FRONTEND_DISPATCH_MAX_AGE", 120.0)
        _cfg.FRONTEND_DISPATCH_MAX_AGE = 5.0  # floor inside the sweep
        self.addCleanup(lambda: setattr(_cfg, "FRONTEND_DISPATCH_MAX_AGE", saved))

        self.phase4.register_dispatch("old", "action-old", tool="open_site")
        with self.phase4._dispatch_lock:
            self.phase4._pending_dispatches["old"]["dispatched_at"] = 0.0
        self.phase4.register_dispatch("new", "action-new", tool="open_site")

        with self.phase4._dispatch_lock:
            keys = set(self.phase4._pending_dispatches)
        self.assertEqual(keys, {"new"})

    def test_no_frontend_action_means_nothing_to_acknowledge(self):
        reg = ToolRegistry()
        reg.register(ToolSpec("quiet", "does nothing visible", lambda: "ok",
                              verification={"family": "query"}))
        coordinator = ExecutionCoordinator(reg, NullMemory(), self.phase4)
        context = ExecutionContext(user_message="be quiet", source="test")
        result = coordinator.execute_action(
            context, coordinator.action("quiet", {}),
            confirmation_already_checked=True)
        self.assertEqual(result.frontend_actions, {})
        self.assertEqual(self.phase4.pending_dispatches(3600), [])


class WaitForVerdictTests(unittest.TestCase):
    """M13 §3.4 -- the turn must be able to wait for the truth, boundedly."""

    def setUp(self):
        self.phase4 = RecordingPhase4()

    def test_verdict_already_present_returns_immediately(self):
        self.phase4._publish_verdict_locally(
            {"action_id": "a1", "tool": "open_site", "verdict": models.PASS})
        got = self.phase4.wait_for_verdict("a1", timeout=0.01)
        self.assertIsNotNone(got)
        self.assertEqual(got["verdict"], models.PASS)

    def test_verdict_arriving_later_wakes_the_waiter(self):
        import threading

        def publish_soon():
            self.phase4._publish_verdict_locally(
                {"action_id": "a2", "tool": "open_site", "verdict": models.FAIL,
                 "reason": "browser rejected the action"})

        threading.Timer(0.05, publish_soon).start()
        got = self.phase4.wait_for_verdict("a2", timeout=2.0)
        self.assertIsNotNone(got)
        self.assertEqual(got["verdict"], models.FAIL)

    def test_timeout_returns_none_and_never_hangs(self):
        import time
        t0 = time.perf_counter()
        self.assertIsNone(self.phase4.wait_for_verdict("never", timeout=0.15))
        self.assertLess(time.perf_counter() - t0, 2.0)

    def test_missing_action_id_is_none(self):
        self.assertIsNone(self.phase4.wait_for_verdict("", timeout=0.01))


if __name__ == "__main__":
    unittest.main()
