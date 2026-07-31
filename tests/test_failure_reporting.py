"""FAIL verdicts must reach the user, not just the side panel.

Verification runs after the reply has streamed, so a FAIL arrives once Jarvis
has already said "done". Measured on real usage, 18% of recorded actions were
FAIL and none of them were ever surfaced in the conversation.
"""

import unittest

from app.services.agent.checker import models
from app.services.agent.checker.coordinator import Phase4Coordinator


class FailureMessageTests(unittest.TestCase):
    def test_reason_is_carried_into_the_message(self):
        message = Phase4Coordinator._failure_message(
            {"tool": "wifi_control", "reason": "Wi-Fi is still off", "evidence": ""})
        self.assertIn("Wi-Fi is still off", message)
        self.assertTrue(message.startswith("Actually"))

    def test_evidence_is_appended_when_it_adds_information(self):
        message = Phase4Coordinator._failure_message(
            {"tool": "wifi_control", "reason": "Wi-Fi is still off",
             "evidence": "wifi=disabled"})
        self.assertIn("wifi=disabled", message)

    def test_evidence_duplicating_the_reason_is_not_repeated(self):
        message = Phase4Coordinator._failure_message(
            {"tool": "close_application", "reason": "'Spotify' is still open",
             "evidence": "'Spotify' is still open"})
        self.assertEqual(message.count("still open"), 1)

    def test_a_missing_reason_still_produces_something_usable(self):
        message = Phase4Coordinator._failure_message({"tool": "ui_click"})
        self.assertIn("ui_click", message)
        self.assertTrue(message)

    def test_no_tool_and_no_reason_yields_empty_rather_than_nonsense(self):
        self.assertEqual(Phase4Coordinator._failure_message({}), "")


class ActivityRowTests(unittest.TestCase):
    def setUp(self):
        self.coordinator = Phase4Coordinator()

    def _latest(self):
        rows = self.coordinator.recent_activity(5)
        return rows[0] if rows else {}

    def test_fail_rows_carry_a_user_facing_message(self):
        self.coordinator._remember_verified({
            "tool": "wifi_control", "verdict": models.FAIL,
            "reason": "Wi-Fi is still off", "evidence": "", "source": "watcher",
        })
        row = self._latest()
        self.assertEqual(row["verdict"], models.FAIL)
        self.assertIn("Wi-Fi is still off", row.get("message", ""))

    def test_pass_rows_carry_no_message_so_the_chat_stays_quiet(self):
        self.coordinator._remember_verified({
            "tool": "set_volume", "verdict": models.PASS,
            "reason": "volume is 40", "evidence": "", "source": "watcher",
        })
        self.assertNotIn("message", self._latest())

    def test_unknown_rows_carry_no_message(self):
        self.coordinator._remember_verified({
            "tool": "shutdown_computer", "verdict": models.UNKNOWN,
            "reason": "cannot be verified by design", "evidence": "", "source": "none",
        })
        self.assertNotIn("message", self._latest())

    def test_metrics_still_count_the_verdict(self):
        self.coordinator._remember_verified({
            "tool": "wifi_control", "verdict": models.FAIL, "reason": "still off",
            "evidence": "", "source": "watcher", "ok": True,
        })
        metrics = self.coordinator.verification_metrics()
        self.assertEqual(metrics["total_fail"], 1)
        # tool reported success but the verifier disagreed -- that is the
        # exact case this whole milestone exists to surface
        self.assertEqual(metrics["disagreement_count"], 1)


if __name__ == "__main__":
    unittest.main()
