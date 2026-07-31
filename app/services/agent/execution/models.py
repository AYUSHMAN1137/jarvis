"""Typed, schema-versioned contracts for every JARVIS execution path.

Section 5 of the implementation plan: canonical data contracts shared by every
module.  Serialization methods produce dictionaries for SSE and the event bus.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1


def _id() -> str:
    return uuid.uuid4().hex


def _event_id() -> str:
    """Globally-unique event ID (Section 5.6)."""
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Section 5.6 -- Event envelope helper
# ---------------------------------------------------------------------------
def event_envelope(
    event_type: str,
    execution_id: str = "",
    action_id: str = "",
    **extra: Any,
) -> Dict[str, Any]:
    """Build a schema-versioned event dict for cross-module publishing."""
    env: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "event_id": _event_id(),
        "event_type": event_type,
        "occurred_at": time.time(),
    }
    if execution_id:
        env["execution_id"] = execution_id
    if action_id:
        env["action_id"] = action_id
    env.update(extra)
    return env


# ---------------------------------------------------------------------------
# Section 5 -- Typed contracts
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConfirmationGrant:
    action_id: str
    tool: str
    args_hash: str
    expires_at: float

    def valid(self, action_id: str, tool: str, args_hash: str, now: Optional[float] = None) -> bool:
        return bool(self.action_id == action_id and self.tool == tool and
                    self.args_hash == args_hash and self.expires_at >= (now or time.time()))


@dataclass
class ExecutionContext:
    user_message: str
    source: str
    session_id: str = ""
    turn_id: str = ""
    route: str = "task"
    execution_id: str = field(default_factory=_id)
    created_at: float = field(default_factory=time.time)
    confirmation_grants: List[ConfirmationGrant] = field(default_factory=list)
    context_fingerprint: Dict[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION


@dataclass
class ActionSpec:
    tool: str
    args: Dict[str, Any]
    index: int = 1
    action_id: str = field(default_factory=_id)
    depends_on: List[str] = field(default_factory=list)
    risk_level: str = "safe"
    requires_confirmation: bool = False
    verification: Dict[str, Any] = field(default_factory=dict)
    context_dependencies: List[str] = field(default_factory=list)
    fallback: Optional[Dict[str, Any]] = None
    verification_barrier: bool = False
    schema_version: int = SCHEMA_VERSION


@dataclass
class ActionResult:
    execution_id: str
    action_id: str
    tool: str
    args: Dict[str, Any]
    started_at: float
    finished_at: float
    transport_ok: bool
    observation: str
    frontend_actions: Dict[str, Any] = field(default_factory=dict)
    error_type: str = ""
    error_message: str = ""
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Section 5.4 -- VerificationResult
@dataclass
class VerificationResult:
    """Outcome of verifying one action after execution."""
    execution_id: str = ""
    action_id: str = ""
    verdict: str = "UNKNOWN"       # PASS | FAIL | UNKNOWN
    reason: str = ""
    evidence: str = ""
    source: str = "none"           # watcher | vision | tool | none
    confidence: float = 0.0
    verified_at: float = field(default_factory=time.time)
    state_fingerprint: str = ""
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def is_pass(self) -> bool:
        return self.verdict == "PASS"

    @property
    def is_fail(self) -> bool:
        return self.verdict == "FAIL"


# Section 5.5 -- ExecutionManifest
@dataclass
class ExecutionManifest:
    context: ExecutionContext
    actions: List[ActionSpec] = field(default_factory=list)
    results: List[ActionResult] = field(default_factory=list)
    verifications: List[VerificationResult] = field(default_factory=list)
    expected_verification_ids: List[str] = field(default_factory=list)
    status: str = "running"
    final_response: str = ""
    completed_at: Optional[float] = None
    confirmation_history: List[Dict[str, Any]] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION

    @property
    def ok(self) -> bool:
        return bool(self.results and len(self.results) == len(self.actions) and
                    all(r.transport_ok for r in self.results))

    def step_payloads(self) -> List[Dict[str, Any]]:
        return [{"action_id": a.action_id, "tool": a.tool, "args": dict(a.args or {}),
                 "index": a.index} for a in self.actions[:len(self.results)]]
