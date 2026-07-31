"""Behaviour of the verifier families added in M4: query, memory, google, frontend."""

import unittest

from app.services.agent.checker import models
from app.services.agent.checker.checker import Checker
from app.services.agent.tools import load_all_tools

# Family routing reads the tool registry, so it has to be populated first.
load_all_tools()


class FakeMemory:
    enabled = True

    def __init__(self, hits=""):
        self.hits = hits

    def recall(self, query, limit=4):
        return self.hits


class QueryFamilyTests(unittest.TestCase):
    def setUp(self):
        self.checker = Checker()

    def test_read_only_tool_passes_on_transport_evidence(self):
        result = self.checker._verify_query("battery_status", {}, {})
        self.assertEqual(result.verdict, models.PASS)
        self.assertEqual(result.source, "transport")

    def test_the_reason_does_not_overclaim(self):
        """It must not read as if a state change was confirmed."""
        reason = self.checker._verify_query("list_processes", {}, {}).reason.lower()
        self.assertIn("no state change", reason)

    def test_query_routes_through_verify(self):
        result = self.checker.verify("battery_status", {}, {})
        self.assertEqual(result.verdict, models.PASS)


class NoneFamilyTests(unittest.TestCase):
    def test_unverifiable_by_design_is_unknown_not_pass(self):
        result = Checker().verify("shutdown_computer", {}, {})
        self.assertEqual(result.verdict, models.UNKNOWN)
        self.assertIn("cannot be verified", result.reason)

    def test_it_is_distinguishable_from_an_unclassified_tool(self):
        checker = Checker()
        by_design = checker.verify("sleep_computer", {}, {})
        unclassified = checker.verify("some_unregistered_tool", {}, {})
        self.assertEqual(by_design.source, "none")
        self.assertEqual(unclassified.source, "watcher")


class MemoryFamilyTests(unittest.TestCase):
    def _checker(self, memory):
        checker = Checker()
        checker._mem_for_test = memory
        import app.services.memory_service as ms
        self._saved = ms.get_memory
        ms.get_memory = lambda: memory
        self.addCleanup(lambda: setattr(ms, "get_memory", self._saved))
        return checker

    def test_a_stored_fact_reads_back_as_pass(self):
        checker = self._checker(FakeMemory(hits="- name: Ayush"))
        result = checker._verify_memory("remember", {"text": "Ayush"}, {})
        self.assertEqual(result.verdict, models.PASS)

    def test_a_fact_that_did_not_land_is_a_fail(self):
        checker = self._checker(FakeMemory(hits=""))
        result = checker._verify_memory("remember", {"text": "Ayush"}, {})
        self.assertEqual(result.verdict, models.FAIL)

    def test_forget_inverts_the_check(self):
        checker = self._checker(FakeMemory(hits="- name: Ayush"))
        result = checker._verify_memory("forget", {"query": "Ayush"}, {})
        self.assertEqual(result.verdict, models.FAIL)  # still present -> failed

        checker = self._checker(FakeMemory(hits=""))
        result = checker._verify_memory("forget", {"query": "Ayush"}, {})
        self.assertEqual(result.verdict, models.PASS)

    def test_nothing_to_look_up_is_unknown_not_pass(self):
        checker = self._checker(FakeMemory())
        result = checker._verify_memory("remember", {}, {})
        self.assertEqual(result.verdict, models.UNKNOWN)


class FrontendFamilyTests(unittest.TestCase):
    def _with_phase4(self, result):
        import app.services.agent.checker as pkg

        class FakePhase4:
            def dispatch_result(self, action_id):
                return result

        saved = pkg.get_phase4
        pkg.get_phase4 = lambda: FakePhase4()
        self.addCleanup(lambda: setattr(pkg, "get_phase4", saved))
        return Checker()

    def test_accepted_ack_is_a_pass(self):
        checker = self._with_phase4({"accepted": True, "attempted": True, "error": ""})
        result = checker._verify_frontend("open_website", {"url": "example.com"}, "a1")
        self.assertEqual(result.verdict, models.PASS)
        self.assertEqual(result.source, "frontend")

    def test_no_ack_is_unknown_never_pass(self):
        checker = self._with_phase4(None)
        result = checker._verify_frontend("open_website", {"url": "example.com"}, "a1")
        self.assertEqual(result.verdict, models.UNKNOWN)

    def test_a_rejected_dispatch_is_a_fail_with_the_reason(self):
        checker = self._with_phase4(
            {"accepted": False, "attempted": True, "error": "popup blocked"})
        result = checker._verify_frontend("open_website", {"url": "example.com"}, "a1")
        self.assertEqual(result.verdict, models.FAIL)
        self.assertIn("popup blocked", result.reason)


class GoogleFamilyTests(unittest.TestCase):
    def _with_calendar(self, hits, has_service=True):
        from app.services.agent import deps

        class FakeCalendar:
            def search_events_summary(self, title, max_results=10):
                # Mirrors the real API: formatted text, not a list.
                if not hits:
                    return "No events found."
                return "Events:\n" + "\n".join(h.get("summary", "") for h in hits)

        saved = getattr(deps, "calendar_service", None)
        deps.calendar_service = FakeCalendar() if has_service else None
        self.addCleanup(lambda: setattr(deps, "calendar_service", saved))
        return Checker()

    def test_a_created_event_that_exists_is_a_pass(self):
        checker = self._with_calendar([{"summary": "Dentist"}])
        result = checker._verify_google("calendar_create", {"title": "Dentist"}, {})
        self.assertEqual(result.verdict, models.PASS)

    def test_a_created_event_that_is_missing_is_a_fail(self):
        checker = self._with_calendar([])
        result = checker._verify_google("calendar_create", {"title": "Dentist"}, {})
        self.assertEqual(result.verdict, models.FAIL)

    def test_delete_inverts_the_check(self):
        checker = self._with_calendar([])
        result = checker._verify_google("calendar_delete", {"title": "Dentist"}, {})
        self.assertEqual(result.verdict, models.PASS)

    def test_no_title_is_unknown_rather_than_assumed_success(self):
        checker = self._with_calendar([])
        result = checker._verify_google("calendar_create", {}, {})
        self.assertEqual(result.verdict, models.UNKNOWN)

    def test_missing_service_is_unknown(self):
        checker = self._with_calendar([], has_service=False)
        result = checker._verify_google("calendar_create", {"title": "X"}, {})
        self.assertEqual(result.verdict, models.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
