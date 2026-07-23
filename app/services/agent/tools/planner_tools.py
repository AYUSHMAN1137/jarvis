"""Phase 5 -- the multi-step planning tool.

Gives the agent ONE tool, `do_multistep`, for commands that are really several
actions that each need to be checked ("turn on wifi then set brightness to 50",
"open settings and turn off bluetooth", "play <song> and make sure it's
playing"). Internally it:
  1. asks the Planner to break the goal into ordered, tool-mapped steps,
  2. runs them through the Executor (precondition-skip, confirm gate, verify per
     step, honest stop),
  3. returns a short, truthful summary of what actually happened.

If the planner can't produce a plan, it returns an ERROR string so the agent
simply handles the actions itself (single-action fallback) -- nothing breaks.
"""

from __future__ import annotations

import logging

from app.services.agent.tool_registry import tool

logger = logging.getLogger("J.A.R.V.I.S")


@tool(
    name="do_multistep",
    description=(
        "Run a command that needs SEVERAL verified steps in order -- e.g. 'wifi "
        "on karo aur brightness 50', 'open settings and turn off bluetooth', or "
        "'play <song> and confirm it's playing'. It plans the steps, does each "
        "one, checks it actually worked, skips steps that are already done, and "
        "pauses for confirmation before anything risky. Use this instead of "
        "firing the individual tools yourself when there are 2+ dependent steps."
    ),
    params={
        "goal": {"type": "string", "description": "The full multi-step command in the user's own words."},
        "confirm": {
            "type": "boolean",
            "description": "Set true ONLY after the user approved a risky step the plan paused on.",
            "required": False,
        },
    },
    category="system",
)
def do_multistep(goal: str, confirm: bool = False) -> str:
    goal = (goal or "").strip()
    if not goal:
        return "ERROR: empty goal."
    try:
        from app.services.agent.planner import get_phase5
        result = get_phase5().run(goal, confirmed=bool(confirm))
    except Exception as e:  # noqa: BLE001
        logger.warning("[PHASE5] do_multistep failed: %s", " ".join(str(e).split())[:80])
        return "ERROR: multi-step planner unavailable; handle the steps individually."
    if result is None:
        # NOT an error -- the planner simply judged this is better handled as
        # single actions. Return a plain (non-"ERROR:") message so the checker
        # does NOT log a false failure; the agent just proceeds normally.
        return ("No multi-step plan needed; handle the actions individually "
                "with the normal tools.")
    if getattr(result, "paused", False):
        step = getattr(result, "pending_step", None)
        what = step.description if step is not None else "a risky step"
        return (f"Paused before a risky step: {what}. Ask the user to confirm, "
                f"then call do_multistep again with confirm=true.\n"
                f"Progress so far: {result.summary}")
    prefix = "All steps completed." if result.ok else "Stopped early (not everything worked)."
    return f"{prefix} {result.summary}"
