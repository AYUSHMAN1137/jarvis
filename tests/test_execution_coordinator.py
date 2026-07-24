import unittest

from app.services.agent.execution import (
    ConfirmationRequired, ExecutionContext, ExecutionCoordinator,
)
from app.services.agent.tool_registry import ToolParam, ToolRegistry, ToolSpec


class FakeMemory:
    def __init__(self): self.rows = []
    def record_action(self, tool, args, ok=True, **meta):
        self.rows.append((tool, args, ok, meta))


class FakePhase4:
    def __init__(self): self.actions = []; self.completed = []
    def publish_action_done(self, tool, args, observation, user_message="", **meta):
        self.actions.append((tool, args, observation, user_message, meta))
    def publish_execution_completed(self, execution_id, user_message, steps, ok):
        self.completed.append((execution_id, user_message, steps, ok))


def make_registry(calls):
    reg = ToolRegistry()
    reg.register(ToolSpec("good", "ok", lambda value: calls.append(value) or "done",
                          [ToolParam("value", "int")]))
    reg.register(ToolSpec("bad", "bad", lambda: "ERROR: failed"))
    reg.register(ToolSpec("danger", "danger", lambda: "done", dangerous=True,
                          risk_level="dangerous"))
    return reg


class ExecutionCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.calls = []; self.memory = FakeMemory(); self.p4 = FakePhase4()
        self.coordinator = ExecutionCoordinator(make_registry(self.calls), self.memory, self.p4)

    def test_atomic_execution_records_and_publishes_once(self):
        ctx = ExecutionContext("do it", "agent")
        manifest = self.coordinator.execute_plan(ctx, [self.coordinator.action("good", {"value": 4})])
        self.assertTrue(manifest.ok)
        self.assertEqual(self.calls, [4])
        self.assertEqual(len(self.memory.rows), 1)
        self.assertEqual(len(self.p4.actions), 1)
        self.assertEqual(len(self.p4.completed), 1)
        self.assertEqual(self.memory.rows[0][3]["execution_id"], ctx.execution_id)

    def test_plan_stops_after_transport_failure(self):
        ctx = ExecutionContext("workflow", "cache_plan")
        actions = [self.coordinator.action("bad", {}, 1),
                   self.coordinator.action("good", {"value": 9}, 2)]
        manifest = self.coordinator.execute_plan(ctx, actions)
        self.assertFalse(manifest.ok)
        self.assertEqual(len(manifest.results), 1)
        self.assertEqual(self.calls, [])

    def test_argument_validation_prevents_execution(self):
        ctx = ExecutionContext("bad args", "agent")
        result = self.coordinator.execute_action(ctx, self.coordinator.action("good", {"value": "4"}))
        self.assertFalse(result.transport_ok)
        self.assertEqual(result.error_type, "validation")
        self.assertEqual(self.calls, [])

    def test_dangerous_action_requires_scoped_confirmation(self):
        ctx = ExecutionContext("danger", "agent")
        with self.assertRaises(ConfirmationRequired):
            self.coordinator.execute_action(ctx, self.coordinator.action("danger", {}))


if __name__ == "__main__": unittest.main()
