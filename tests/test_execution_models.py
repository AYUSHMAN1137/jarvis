"""Quick validation of Stage 1 execution models."""
import sys
sys.path.insert(0, ".")

from app.services.agent.execution.models import (
    ExecutionContext, ActionSpec, ActionResult,
    VerificationResult, ExecutionManifest,
    ExecutionSource, ExecutionStatus, Verdict, RiskLevel,
    make_event, SCHEMA_VERSION,
)

# Basic construction
ec = ExecutionContext(user_message="open chrome", session_id="s1")
assert ec.execution_id, "execution_id should be auto-generated"
assert ec.source == ExecutionSource.AGENT

spec = ActionSpec(tool="open_application", args={"app": "chrome"}, index=0)
assert spec.action_id
assert spec.risk_level == RiskLevel.SAFE

result = ActionResult(
    execution_id=ec.execution_id,
    action_id=spec.action_id,
    tool="open_application",
    transport_ok=True,
    observation="Opened Chrome",
    started_at=1000.0,
    finished_at=1001.5,
)
assert result.duration_ms == 1500.0

vr = VerificationResult(
    execution_id=ec.execution_id,
    action_id=spec.action_id,
    verdict=Verdict.PASS,
    reason="process found",
    source="state_checker",
)

manifest = ExecutionManifest(
    execution_id=ec.execution_id,
    user_message="open chrome",
    source=ExecutionSource.AGENT,
    actions=[spec],
    results=[result],
    verifications=[vr],
)
assert manifest.all_transport_ok
assert manifest.all_verified_pass
assert not manifest.has_unknown
assert not manifest.has_fail

# Serialization
d = manifest.to_dict()
assert d["schema_version"] == SCHEMA_VERSION
assert d["source"] == "agent"
assert d["status"] == "pending"
assert len(d["actions"]) == 1
assert d["actions"][0]["risk_level"] == "safe"

# Event envelope
evt = make_event("execution.completed", ec.execution_id, spec.action_id, {"ok": True})
assert evt["event_type"] == "execution.completed"
assert evt["schema_version"] == SCHEMA_VERSION
assert evt["execution_id"] == ec.execution_id

print("ALL STAGE 1 MODEL CHECKS PASSED")
