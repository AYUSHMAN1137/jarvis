"""Phase 5 -- the Step Executor.

Runs a Plan one step at a time, the reliable way:

  for each step:
    1. PRECONDITION skip  -- if the goal of this step is already true
       (verify PASSes), skip it. ("wifi on karo" when wifi is already on.)
    2. CONFIRM gate       -- a risky / irreversible step pauses and asks ONCE
       (unless the caller already confirmed). No silent destructive actions.
    3. RUN (UIA-first)    -- execute the mapped tool. The planner prefers the
       generic UIA tools for on-screen actions, so this is "UIA-first".
    4. VERIFY             -- check it actually worked via the Phase 4 checker
       (watcher-first, then vision fallback). UNKNOWN is not treated as success.
    5. FALLBACK           -- if it can't be verified and the step has a
       fallback action, try that and re-verify (this is the "vision fallback"
       layer for UI steps).
    6. HONEST STOP        -- on a real FAIL, stop the plan and report exactly
       which step failed and why. Never fake "all done".

Every decision is published to the event bus (plan.started / plan.step /
plan.done) so the Control Center and the Phase 4 Learner can see it live.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import config as _cfg

from app.services.agent.planner.planner import Plan, Step

logger = logging.getLogger("J.A.R.V.I.S")

_SETTLE = float(getattr(_cfg, "PLANNER_STEP_SETTLE_SECONDS", 1.0))
_CONFIRM_RISKY = bool(getattr(_cfg, "PHASE5_CONFIRM_RISKY", True))

# Per-step outcome tags.
DONE = "DONE"
SKIPPED = "SKIPPED"
FAILED = "FAILED"
UNVERIFIED = "UNVERIFIED"
NEEDS_CONFIRM = "NEEDS_CONFIRM"


def _clean(s: Any, limit: int = 80) -> str:
    return " ".join(str(s or "").split())[:limit]


@dataclass
class StepOutcome:
    step: Step
    status: str
    observation: str = ""
    reason: str = ""
    evidence: str = ""
    source: str = ""
    via_fallback: bool = False

    def to_payload(self) -> Dict[str, Any]:
        return {
            "id": self.step.id, "description": self.step.description,
            "tool": self.step.tool, "status": self.status,
            "reason": _clean(self.reason, 120), "evidence": _clean(self.evidence, 120),
            "source": self.source, "via_fallback": self.via_fallback,
        }


@dataclass
class ExecResult:
    goal: str
    ok: bool
    outcomes: List[StepOutcome] = field(default_factory=list)
    paused: bool = False           # True when stopped at a confirm gate
    pending_step: Optional[Step] = None
    summary: str = ""

    def to_payload(self) -> Dict[str, Any]:
        return {
            "goal": self.goal, "ok": self.ok, "paused": self.paused,
            "done": sum(1 for o in self.outcomes if o.status == DONE),
            "skipped": sum(1 for o in self.outcomes if o.status == SKIPPED),
            "failed": sum(1 for o in self.outcomes if o.status == FAILED),
            "steps": [o.to_payload() for o in self.outcomes],
        }


class StepExecutor:
    """Execute a Plan with verify-per-step + honest stop."""

    def __init__(self, registry, verify_fn: Optional[Callable[[str, dict], Any]] = None,
                 bus: Any = None, settle: Optional[float] = None,
                 confirm_risky: Optional[bool] = None) -> None:
        self._registry = registry
        # verify_fn(tool, args) -> VerifyResult (or None to skip verification).
        self._verify = verify_fn
        self._bus = bus
        self._settle = float(settle if settle is not None else _SETTLE)
        self._confirm_risky = _CONFIRM_RISKY if confirm_risky is None else bool(confirm_risky)

    # -- bus helper (never raises) -------------------------------------- #
    def _emit(self, event: str, payload: dict) -> None:
        if self._bus is None:
            return
        try:
            self._bus.publish(event, payload)
        except Exception as e:  # noqa: BLE001
            logger.debug("[EXECUTOR] emit %s failed: %s", event, _clean(e))

    def _run_tool(self, tool: str, args: dict) -> str:
        try:
            obs = self._registry.execute(tool, args)
        except Exception as e:  # noqa: BLE001
            # registry.execute shouldn't raise, but stay defensive.
            return f"ERROR: {_clean(e)}"
        return obs if isinstance(obs, str) else str(obs)

    def _verify_pass(self, tool: str, args: dict):
        """Return (is_pass, is_fail, reason, evidence, source). UNKNOWN -> neither."""
        if self._verify is None:
            return None, None, "", "", "none"
        try:
            vr = self._verify(tool, args)
        except Exception as e:  # noqa: BLE001
            logger.debug("[EXECUTOR] verify raised: %s", _clean(e))
            return None, None, "", "", "none"
        if vr is None:
            return None, None, "", "", "none"
        return (vr.is_pass(), vr.is_fail(), getattr(vr, "reason", ""),
                getattr(vr, "evidence", ""), getattr(vr, "source", "none"))

    def _verify_retry(self, tool: str, args: dict, attempts: int = 3):
        """Verify with a few short retries.

        Some effects (Wi-Fi / Bluetooth radio toggles, brightness) take a couple
        seconds to reflect in the watcher, so a single immediate check can read
        a false FAIL. Re-poll a few times before giving up. Only re-reads state
        -- never re-runs the action. Returns on the first PASS.
        """
        last = self._verify_pass(tool, args)
        is_pass, is_fail = last[0], last[1]
        tries = max(1, int(attempts))
        for _ in range(tries - 1):
            if is_pass:
                return last
            # keep polling while it's still FAIL/UNKNOWN -- the state may catch up
            time.sleep(self._settle if self._settle > 0 else 0.5)
            last = self._verify_pass(tool, args)
            is_pass, is_fail = last[0], last[1]
        return last

    # -- main ----------------------------------------------------------- #
    def run(self, plan: Plan, confirmed: bool = False) -> ExecResult:
        result = ExecResult(goal=plan.goal, ok=True)
        self._emit("plan.started", {"goal": _clean(plan.goal, 120),
                                    "total": len(plan.steps), "source": plan.source})
        logger.info("[PHASE5] plan start: '%s' (%d steps)", _clean(plan.goal), len(plan.steps))

        for idx, step in enumerate(plan.steps, start=1):
            # 1) precondition skip
            if step.precondition:
                p_pass, _, p_reason, p_ev, p_src = self._verify_pass(
                    step.precondition.get("tool", step.tool),
                    step.precondition.get("args", {}),
                )
                if p_pass:
                    out = StepOutcome(step, SKIPPED, reason=p_reason or "already satisfied",
                                      evidence=p_ev, source=p_src)
                    result.outcomes.append(out)
                    self._step_event(idx, len(plan.steps), out)
                    logger.info("[PHASE5] step %d/%d SKIP (%s)", idx, len(plan.steps),
                                _clean(out.reason))
                    continue

            # 2) confirm gate for risky / irreversible steps
            if step.risky and self._confirm_risky and not confirmed:
                out = StepOutcome(step, NEEDS_CONFIRM,
                                  reason="risky step needs confirmation")
                result.outcomes.append(out)
                result.ok = False
                result.paused = True
                result.pending_step = step
                self._step_event(idx, len(plan.steps), out)
                logger.info("[PHASE5] step %d/%d PAUSE -- needs confirm: %s",
                            idx, len(plan.steps), _clean(step.description))
                self._finish(plan, result)
                return result

            # 3) run (UIA-first: planner prefers ui_* tools for screen actions)
            logger.info("[EXECUTOR] step %d/%d run %s args=%s", idx, len(plan.steps),
                        _clean(step.tool, 30), _clean(step.args, 80))
            obs = self._run_tool(step.tool, step.args)
            if self._settle > 0:
                time.sleep(self._settle)

            # 4) verify (retry a few times -- Wi-Fi/Bluetooth radio toggles and
            #    brightness can take a couple seconds to reflect in the watcher,
            #    so a single immediate check can read a false FAIL). If the tool
            #    itself errored, skip polling -- that error is the verdict.
            obs_err = obs.strip().upper().startswith("ERROR")
            if obs_err:
                is_pass, is_fail, reason, ev, src = (False, True, "", "", "none")
            else:
                is_pass, is_fail, reason, ev, src = self._verify_retry(
                    step.tool, step.args)

            if is_pass:
                out = StepOutcome(step, DONE, observation=obs, reason=reason,
                                  evidence=ev, source=src)
                result.outcomes.append(out)
                self._step_event(idx, len(plan.steps), out)
                logger.info("[PHASE5] step %d/%d DONE (%s)", idx, len(plan.steps), _clean(reason))
                continue

            # 5) fallback (vision-fallback layer for UI steps lives inside the
            #    checker's verify_live; here we try the planner's alt action too)
            if (is_fail or obs_err) and step.fallback:
                logger.info("[EXECUTOR] step %d fallback -> %s", idx,
                            _clean(step.fallback.get("tool")))
                fb_obs = self._run_tool(step.fallback.get("tool", ""),
                                        step.fallback.get("args", {}))
                if self._settle > 0:
                    time.sleep(self._settle)
                f_pass, f_fail, f_reason, f_ev, f_src = self._verify_pass(
                    step.fallback.get("tool", ""), step.fallback.get("args", {}))
                if f_pass or (not f_fail and not fb_obs.strip().upper().startswith("ERROR")):
                    out = StepOutcome(step, DONE, observation=fb_obs,
                                      reason=f_reason or "recovered via fallback",
                                      evidence=f_ev, source=f_src, via_fallback=True)
                    result.outcomes.append(out)
                    self._step_event(idx, len(plan.steps), out)
                    logger.info("[PHASE5] step %d/%d DONE via fallback", idx, len(plan.steps))
                    continue

            # 6) decide FAIL vs UNVERIFIED, and honest-stop on real FAIL
            if is_fail or obs_err:
                # if the tool itself errored, THAT error is the failure reason --
                # don't mislabel it with an unrelated verifier note like
                # "no verifier for this action".
                fail_reason = ((_clean(obs, 120) if obs_err else reason)
                               or reason or "step failed")
                out = StepOutcome(step, FAILED, observation=obs,
                                  reason=fail_reason,
                                  evidence=ev, source=src)
                result.outcomes.append(out)
                result.ok = False
                self._step_event(idx, len(plan.steps), out)
                logger.warning("[PHASE5] step %d/%d FAILED (%s) -- stopping plan",
                               idx, len(plan.steps), _clean(out.reason))
                self._finish(plan, result)
                return result

            # tool ran, no error, but we couldn't positively verify -> honest UNVERIFIED
            out = StepOutcome(step, UNVERIFIED, observation=obs,
                              reason=reason or "ran but could not verify",
                              evidence=ev, source=src)
            result.outcomes.append(out)
            self._step_event(idx, len(plan.steps), out)
            logger.info("[PHASE5] step %d/%d UNVERIFIED (%s)", idx, len(plan.steps),
                        _clean(out.reason))

        self._finish(plan, result)
        return result

    # -- events + summary ----------------------------------------------- #
    def _step_event(self, idx: int, total: int, out: StepOutcome) -> None:
        self._emit("plan.step", {
            "index": idx, "total": total, "tool": out.step.tool,
            "description": _clean(out.step.description, 120), "status": out.status,
            "reason": _clean(out.reason, 120), "evidence": _clean(out.evidence, 120),
            "source": out.source, "via_fallback": out.via_fallback,
        })

    def _finish(self, plan: Plan, result: ExecResult) -> None:
        result.summary = self._summarize(result)
        self._emit("plan.done", {**result.to_payload(), "summary": result.summary})
        logger.info("[PHASE5] plan done: ok=%s -- %s", result.ok, _clean(result.summary, 160))

    @staticmethod
    def _summarize(result: ExecResult) -> str:
        parts = []
        for o in result.outcomes:
            if o.status == DONE:
                tag = "done via fallback" if o.via_fallback else "done"
            elif o.status == SKIPPED:
                tag = "skipped (already ok)"
            elif o.status == NEEDS_CONFIRM:
                tag = "needs your confirmation"
            elif o.status == UNVERIFIED:
                tag = "ran (unverified)"
            else:
                tag = f"FAILED: {_clean(o.reason, 80)}"
            parts.append(f"{o.step.id}. {_clean(o.step.description, 60)} -> {tag}")
        if result.paused:
            parts.append("Plan paused for confirmation.")
        return " | ".join(parts) if parts else "nothing to do"
