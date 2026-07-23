"""Phase 5 -- Planner + Executor package.

Import-light by design. The pieces:
  * planner.py  -- turn ONE natural command into an ordered list of steps.
  * executor.py -- run those steps with precondition skip-checks, a smart
                   confirmation gate, per-step verification and an honest stop.
  * coordinator.py -- a small singleton that wires Planner+Executor to the
                   shared event bus + Phase 4 checker, and feeds the dashboard.
"""

from __future__ import annotations

from app.services.agent.planner.planner import Planner, Plan, Step
from app.services.agent.planner.executor import StepExecutor

__all__ = ["Planner", "Plan", "Step", "StepExecutor", "get_phase5"]


def get_phase5():
    """Return the process-wide Phase 5 coordinator (lazy import)."""
    from app.services.agent.planner.coordinator import get_phase5 as _g
    return _g()
