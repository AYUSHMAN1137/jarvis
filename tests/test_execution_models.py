"""Quick validation of Stage 1 execution models."""
import sys
import unittest
sys.path.insert(0, ".")

from app.services.agent.execution.models import (
    ExecutionContext, ActionSpec, ActionResult,
    VerificationResult, ExecutionManifest,
    ConfirmationGrant, event_envelope, SCHEMA_VERSION,
)


class TestExecutionModels(unittest.TestCase):

    def test_execution_context_auto_ids(self):
        ec = ExecutionContext(user_message="open chrome", source="agent", session_id="s1")
        self.assertTrue(ec.execution_id, "execution_id should be auto-generated")
        self.assertEqual(ec.source, "agent")
        self.assertEqual(ec.schema_version, SCHEMA_VERSION)

    def test_action_spec_defaults(self):
        spec = ActionSpec(tool="open_application", args={"app": "chrome"}, index=1)
        self.assertTrue(spec.action_id)
        self.assertEqual(spec.risk_level, "safe")
        self.assertFalse(spec.requires_confirmation)
        self.assertFalse(spec.verification_barrier)

    def test_action_result_transport(self):
        result = ActionResult(
            execution_id="ex1", action_id="a1",
            tool="open_application", args={"app": "chrome"},
            transport_ok=True, observation="Opened Chrome",
            started_at=1000.0, finished_at=1001.5,
        )
        self.assertTrue(result.transport_ok)
        d = result.to_dict()
        self.assertEqual(d["tool"], "open_application")

    def test_verification_result(self):
        vr = VerificationResult(
            execution_id="ex1", action_id="a1",
            verdict="PASS", reason="process found",
            source="watcher", confidence=0.95,
        )
        self.assertTrue(vr.is_pass)
        self.assertFalse(vr.is_fail)

    def test_execution_manifest_ok(self):
        ctx = ExecutionContext(user_message="test", source="agent")
        spec = ActionSpec(tool="set_volume", args={"level": 50})
        result = ActionResult(
            execution_id=ctx.execution_id, action_id=spec.action_id,
            tool="set_volume", args={"level": 50},
            transport_ok=True, observation="Done",
            started_at=1.0, finished_at=2.0,
        )
        manifest = ExecutionManifest(
            context=ctx, actions=[spec], results=[result],
            status="completed",
        )
        self.assertTrue(manifest.ok)
        payloads = manifest.step_payloads()
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["tool"], "set_volume")

    def test_manifest_not_ok_on_failure(self):
        ctx = ExecutionContext(user_message="test", source="agent")
        spec = ActionSpec(tool="set_volume", args={"level": 50})
        result = ActionResult(
            execution_id=ctx.execution_id, action_id=spec.action_id,
            tool="set_volume", args={"level": 50},
            transport_ok=False, observation="ERROR: failed",
            started_at=1.0, finished_at=2.0,
        )
        manifest = ExecutionManifest(
            context=ctx, actions=[spec], results=[result],
            status="failed",
        )
        self.assertFalse(manifest.ok)

    def test_confirmation_grant_validation(self):
        import time
        grant = ConfirmationGrant(
            action_id="a1", tool="shutdown",
            args_hash="abc123", expires_at=time.time() + 60,
        )
        self.assertTrue(grant.valid("a1", "shutdown", "abc123"))
        self.assertFalse(grant.valid("a2", "shutdown", "abc123"))
        self.assertFalse(grant.valid("a1", "restart", "abc123"))

    def test_event_envelope(self):
        evt = event_envelope("execution.completed", "ex1", "a1", ok=True)
        self.assertEqual(evt["event_type"], "execution.completed")
        self.assertEqual(evt["schema_version"], SCHEMA_VERSION)
        self.assertEqual(evt["execution_id"], "ex1")
        self.assertTrue(evt["ok"])
        self.assertIn("event_id", evt)
        self.assertIn("occurred_at", evt)


if __name__ == "__main__":
    unittest.main()
