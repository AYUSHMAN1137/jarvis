"""Single lifecycle owner for direct, cached, agent, and planned tool runs."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Iterable, Optional

from app.services.agent.execution.models import (
    ActionResult, ActionSpec, ExecutionContext, ExecutionManifest,
)
from app.services.agent.tool_registry import registry as default_registry
from app.services.debug_logger import dbg

logger = logging.getLogger("J.A.R.V.I.S")
_scope = threading.local()


def trusted_confirmation_for(tool: str) -> bool:
    """True only while the coordinator executes an exact user-approved action."""
    return bool(getattr(_scope, "confirmed", False) and
                getattr(_scope, "tool", "") == str(tool or ""))


class ConfirmationRequired(RuntimeError):
    def __init__(self, action: ActionSpec):
        super().__init__(f"confirmation required for {action.tool}")
        self.action = action


def args_hash(args) -> str:
    raw = json.dumps(args or {}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ExecutionCoordinator:
    def __init__(self, registry=None, memory=None, phase4=None, clock=None) -> None:
        self.registry = registry or default_registry
        self._memory = memory
        self._phase4 = phase4
        self._clock = clock or time.time

    def action(self, tool: str, args: dict, index: int = 1, action_id: str = "") -> ActionSpec:
        spec = self.registry.get(tool)
        dangerous = bool(spec and spec.dangerous)
        verification = dict(getattr(spec, "verification", {}) or {}) if spec else {}
        risk = getattr(spec, "risk_level", "dangerous" if dangerous else "safe") if spec else "unknown"
        return ActionSpec(tool=tool, args=dict(args or {}), index=index,
                          action_id=action_id or ActionSpec(tool="", args={}).action_id,
                          risk_level=risk, requires_confirmation=dangerous,
                          verification=verification)

    def _confirmed(self, context: ExecutionContext, action: ActionSpec) -> bool:
        if not action.requires_confirmation:
            return True
        digest = args_hash(action.args)
        return any(g.valid(action.action_id, action.tool, digest, self._clock())
                   for g in context.confirmation_grants)

    def execute_action(self, context: ExecutionContext, action: ActionSpec,
                       confirmation_already_checked: bool = False) -> ActionResult:
        errors = self.registry.validate_arguments(action.tool, action.args)
        if errors:
            now = self._clock()
            return ActionResult(context.execution_id, action.action_id, action.tool,
                                action.args, now, now, False,
                                "ERROR: invalid arguments: " + "; ".join(errors),
                                error_type="validation", error_message="; ".join(errors))
        if action.requires_confirmation and not confirmation_already_checked and not self._confirmed(context, action):
            raise ConfirmationRequired(action)

        started = self._clock()
        dbg.tool_call(action.tool, action.args, step=action.index)
        _scope.confirmed = bool(confirmation_already_checked)
        _scope.tool = action.tool
        try:
            observation = self.registry.execute(action.tool, action.args)
        finally:
            _scope.confirmed = False
            _scope.tool = ""
        frontend_actions = {}
        try:
            from app.services.agent import action_sink
            if action_sink.has_actions():
                action_sink.attach_dispatch(context.execution_id, action.action_id)
                frontend_actions = action_sink.collect()
        except Exception:
            frontend_actions = {}
        finished = self._clock()
        ok = not str(observation).startswith("ERROR")
        dbg.tool_result(action.tool, observation, ok=ok, step=action.index)
        result = ActionResult(context.execution_id, action.action_id, action.tool,
                              action.args, started, finished, ok, str(observation),
                              frontend_actions=frontend_actions,
                              error_type="" if ok else "tool",
                              error_message="" if ok else str(observation)[:300])
        self._record(context, result)
        return result

    def _record(self, context: ExecutionContext, result: ActionResult) -> None:
        try:
            memory = self._memory
            if memory is None:
                from app.services.memory_service import get_memory
                memory = get_memory()
            memory.record_action(result.tool, result.args, result.transport_ok,
                                 execution_id=context.execution_id,
                                 action_id=result.action_id)
        except Exception as exc:
            logger.debug("[EXECUTION] memory record skipped: %s", exc)
        try:
            p4 = self._phase4
            if p4 is None:
                from app.services.agent.checker import get_phase4
                p4 = get_phase4()
            p4.publish_action_done(result.tool, result.args, result.observation,
                                   context.user_message, execution_id=context.execution_id,
                                   action_id=result.action_id)
        except Exception as exc:
            logger.debug("[EXECUTION] action publish skipped: %s", exc)

    def execute_plan(self, context: ExecutionContext, actions: Iterable[ActionSpec],
                     confirmation_already_checked: bool = False) -> ExecutionManifest:
        manifest = ExecutionManifest(context=context, actions=list(actions))
        dbg.execution_event("started", context.execution_id,
                            {"source": context.source, "actions": len(manifest.actions)})
        for action in manifest.actions:
            result = self.execute_action(context, action, confirmation_already_checked)
            manifest.results.append(result)
            if not result.transport_ok:
                manifest.status = "failed"
                break
        else:
            manifest.status = "completed"
        manifest.completed_at = self._clock()
        self.complete(manifest)
        return manifest

    def complete(self, manifest: ExecutionManifest) -> None:
        try:
            p4 = self._phase4
            if p4 is None:
                from app.services.agent.checker import get_phase4
                p4 = get_phase4()
            p4.publish_execution_completed(manifest.context.execution_id,
                                           manifest.context.user_message,
                                           manifest.step_payloads(), manifest.ok)
        except Exception as exc:
            logger.debug("[EXECUTION] completion publish skipped: %s", exc)
        dbg.execution_event("verification queued", manifest.context.execution_id,
                            {"source": manifest.context.source,
                             "actions": len(manifest.results),
                             "tool_results_ok": manifest.ok})


_singleton: Optional[ExecutionCoordinator] = None


def get_execution_coordinator() -> ExecutionCoordinator:
    global _singleton
    if _singleton is None:
        _singleton = ExecutionCoordinator()
    return _singleton
