"""M13 §3.2 -- two frontend tools in one run must not interfere.

Two bugs lived in the sink's lifecycle:

  A) `collect()` never cleared. `reset()` happened once per run, so in a
     multi-step run step 2's emission re-sent step 1's payload and the browser
     opened the same tab twice.
  B) `attach_dispatch()` overwrote `_meta` on every call, so only the LAST
     action of a run kept its action_id. Every earlier frontend action was
     therefore unacknowledgeable -- a permanent UNKNOWN even after the ack chain
     was fixed.

The fix is to drain the sink per action. These tests are the gate for that.
"""

import threading
import unittest

from app.services.agent import action_sink
from app.services.agent.checker.coordinator import Phase4Coordinator
from app.services.agent.execution import ExecutionContext, ExecutionCoordinator
from app.services.agent.tool_registry import ToolParam, ToolRegistry, ToolSpec


class NullMemory:
    def record_action(self, *args, **kwargs):
        pass


class QuietPhase4(Phase4Coordinator):
    def publish_action_done(self, *args, **kwargs):
        pass

    def publish_execution_completed(self, *args, **kwargs):
        pass


def _registry():
    reg = ToolRegistry()

    def open_site(url: str) -> str:
        action_sink.add_open(url)
        return f"Opening {url}."

    def google(query: str) -> str:
        action_sink.add_google(f"https://www.google.com/search?q={query}")
        return f"Searching for {query}."

    reg.register(ToolSpec("open_site", "open", open_site, category="web",
                          params=[ToolParam("url", "string", "url")],
                          verification={"family": "frontend"}))
    reg.register(ToolSpec("google", "search", google, category="web",
                          params=[ToolParam("query", "string", "query")],
                          verification={"family": "frontend"}))
    return reg


class SinkIsolationTests(unittest.TestCase):
    def setUp(self):
        action_sink.reset()
        self.phase4 = QuietPhase4()
        self.coordinator = ExecutionCoordinator(_registry(), NullMemory(), self.phase4)
        self.context = ExecutionContext(user_message="two web things", source="test")

    def _run(self, tool, args):
        action = self.coordinator.action(tool, args)
        result = self.coordinator.execute_action(
            self.context, action, confirmation_already_checked=True)
        return action, result

    def test_two_web_tools_produce_disjoint_payloads(self):
        _, first = self._run("open_site", {"url": "https://a.example"})
        _, second = self._run("google", {"query": "kittens"})

        self.assertEqual(first.frontend_actions["wopens"], ["https://a.example"])
        # The second emission must NOT carry the first action's URL again.
        self.assertEqual(second.frontend_actions["wopens"], [])
        self.assertEqual(len(second.frontend_actions["googlesearches"]), 1)

    def test_each_action_gets_its_own_dispatch_id_and_action_id(self):
        action_a, first = self._run("open_site", {"url": "https://a.example"})
        action_b, second = self._run("google", {"query": "kittens"})

        meta_a = first.frontend_actions["_meta"]
        meta_b = second.frontend_actions["_meta"]
        self.assertNotEqual(meta_a["dispatch_id"], meta_b["dispatch_id"])
        self.assertEqual(meta_a["action_id"], action_a.action_id)
        self.assertEqual(meta_b["action_id"], action_b.action_id)

    def test_both_actions_are_independently_acknowledgeable(self):
        action_a, first = self._run("open_site", {"url": "https://a.example"})
        action_b, second = self._run("google", {"query": "kittens"})

        self.assertTrue(self.phase4.acknowledge_dispatch(
            first.frontend_actions["_meta"]["dispatch_id"]))
        self.assertTrue(self.phase4.acknowledge_dispatch(
            second.frontend_actions["_meta"]["dispatch_id"]))
        self.assertIsNotNone(self.phase4.dispatch_result(action_a.action_id))
        self.assertIsNotNone(self.phase4.dispatch_result(action_b.action_id))

    def test_no_url_is_emitted_twice_across_a_whole_run(self):
        _, first = self._run("open_site", {"url": "https://a.example"})
        _, second = self._run("open_site", {"url": "https://b.example"})
        emitted = (first.frontend_actions["wopens"]
                   + second.frontend_actions["wopens"])
        self.assertEqual(emitted, ["https://a.example", "https://b.example"])
        self.assertEqual(len(emitted), len(set(emitted)))

    def test_sink_is_empty_after_the_coordinator_drains_it(self):
        self._run("open_site", {"url": "https://a.example"})
        self.assertFalse(action_sink.has_actions(),
                         "execute_action must hand the payload over, not copy it")


class SinkPrimitiveTests(unittest.TestCase):
    def setUp(self):
        action_sink.reset()

    def test_collect_with_drain_empties_the_bucket(self):
        action_sink.add_open("https://a.example")
        drained = action_sink.collect(drain=True)
        self.assertEqual(drained["wopens"], ["https://a.example"])
        self.assertFalse(action_sink.has_actions())

    def test_collect_without_drain_leaves_the_bucket_alone(self):
        action_sink.add_open("https://a.example")
        action_sink.collect()
        self.assertTrue(action_sink.has_actions())

    def test_attach_dispatch_returns_none_when_there_is_nothing_to_do(self):
        self.assertIsNone(action_sink.attach_dispatch("exec", "action"))

    def test_attach_dispatch_returns_the_id_it_minted(self):
        action_sink.add_open("https://a.example")
        dispatch_id = action_sink.attach_dispatch("exec", "action")
        self.assertTrue(dispatch_id)
        self.assertEqual(action_sink.collect()["_meta"]["dispatch_id"], dispatch_id)

    def test_meta_alone_does_not_make_a_bucket_look_busy(self):
        bucket = {"_meta": {"dispatch_id": "x"}, "wopens": [], "panels": {}}
        self.assertFalse(action_sink.bucket_has_actions(bucket))

    def test_panels_count_as_actions(self):
        action_sink.set_panel("notes", {"action": "open"})
        self.assertTrue(action_sink.has_actions())

    def test_still_thread_isolated(self):
        results = {}

        def worker(key, url):
            action_sink.reset()
            action_sink.add_open(url)
            results[key] = action_sink.collect(drain=True)["wopens"]

        threads = [threading.Thread(target=worker, args=("a", "https://a.example")),
                   threading.Thread(target=worker, args=("b", "https://b.example"))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results["a"], ["https://a.example"])
        self.assertEqual(results["b"], ["https://b.example"])


if __name__ == "__main__":
    unittest.main()
