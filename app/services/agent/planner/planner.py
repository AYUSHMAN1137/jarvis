"""Phase 5 -- the Planner.

Turns ONE natural-language command into an ordered list of concrete, verifiable
steps that map to REAL tools already in the registry.

Example:  "wifi on karo aur brightness 50 kardo"
  -> [ wifi_control(on=True), set_brightness(level=50) ]

Design (locked decisions)
-------------------------
* LINEAR plan only -- an ordered list with optional per-step precondition (skip
  if already satisfied) and an optional fallback action. This is NOT a heavy
  rule/branch engine; the agent's ReAct loop remains the single-action fallback.
* No hardcode -- decomposition is LLM-driven from the LIVE tool catalogue, so
  new tools are usable immediately and nothing is keyword-matched.
* Fail-soft -- if there is no LLM, the model returns junk, or nothing maps to a
  real tool, make_plan() returns an EMPTY plan. An empty plan means "let the
  normal agent handle it" -- we never block or guess.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import config as _cfg
from app.services.debug_logger import dbg

logger = logging.getLogger("J.A.R.V.I.S")

_MAX_STEPS = int(getattr(_cfg, "PLANNER_MAX_STEPS", 8))


def _clean(s: Any, limit: int = 80) -> str:
    return " ".join(str(s or "").split())[:limit]


@dataclass
class Step:
    """One concrete, verifiable action in a plan."""

    description: str
    tool: str
    args: Dict[str, Any] = field(default_factory=dict)
    risky: bool = False
    # If this verify already PASSes, the step is skipped (precondition met).
    precondition: Optional[Dict[str, Any]] = None     # {"tool":..., "args":...}
    # Alternate action to try if the primary step can't be verified.
    fallback: Optional[Dict[str, Any]] = None         # {"tool":..., "args":...}
    id: int = 0

    def to_payload(self) -> Dict[str, Any]:
        return {
            "id": self.id, "description": self.description, "tool": self.tool,
            "args": self.args, "risky": self.risky,
            "has_fallback": self.fallback is not None,
        }


@dataclass
class Plan:
    goal: str
    steps: List[Step] = field(default_factory=list)
    source: str = "fallback"   # "llm" | "fallback"

    def is_empty(self) -> bool:
        return not self.steps

    def is_multistep(self) -> bool:
        return len(self.steps) >= 2

    def to_payload(self) -> Dict[str, Any]:
        return {"goal": self.goal, "source": self.source,
                "total": len(self.steps), "steps": [s.to_payload() for s in self.steps]}


_SYSTEM_PROMPT = (
    "You are the PLANNER for a Windows voice assistant. Break the user's command "
    "into the SMALLEST ordered list of concrete steps, where each step is ONE "
    "call to ONE of the available tools. Rules:\n"
    "- Use ONLY the tool names listed. Never invent tools or arguments.\n"
    "- If the command is really just one action, return exactly ONE step.\n"
    "- Keep the order the user implied (do X then Y).\n"
    "- For a step whose effect can already be 'true' (e.g. turn wifi on), you MAY "
    "add a 'precondition' = the same tool+args; it will be skipped if already "
    "satisfied.\n"
    "- You MAY add a 'fallback' tool+args to try if the main step fails.\n"
    "- Mark 'risky': true for irreversible/destructive steps (delete, close-all, "
    "send, purchase, shutdown).\n"
    "- THE PLAN MUST REACH THE GOAL. Opening a window is never the last step when "
    "the user asked for something to change inside it. 'turn off night light' is "
    "NOT 'open Settings' -- it is open the page, then ui_do(target='Night light', "
    "action='toggle', state=false). Use ui_do for anything that must be clicked, "
    "toggled or typed on screen; it finds the control itself, including by "
    "searching and scrolling.\n"
    "- Never produce a step whose description tells the USER to do it manually. If "
    "you cannot reach the goal with the available tools, return {\"steps\": []}.\n"
    "Respond with STRICT JSON only:\n"
    '{"steps":[{"description":"...","tool":"tool_name","args":{...},'
    '"risky":false,"precondition":null,"fallback":null}]}'
)


# Verbs that mean "change something", as opposed to "show me something". If the
# goal contains one of these, a plan made only of open/launch steps has not done
# the job. Kept as vocabulary rather than per-feature rules so it generalises.
_CHANGE_VERBS = (
    "turn on", "turn off", "switch on", "switch off", "enable", "disable",
    "toggle", "click", "press", "select", "choose", "change", "set", "increase",
    "decrease", "raise", "lower", "mute", "unmute", "play", "pause", "save",
    "rename", "delete", "install", "update", "connect", "disconnect", "check for",
    "type", "write", "send", "submit", "apply", "chalu", "band", "kar do", "karo",
    "badal", "chala", "bandh",
)
try:
    _CHANGE_VERB_RE = re.compile(
        r"\b(?:" + "|".join(re.escape(v) for v in _CHANGE_VERBS) + r")\b"
    )
except re.error:  # pragma: no cover - defensive
    _CHANGE_VERB_RE = None
# Steps that only surface a window.
_OPEN_ONLY_TOOLS = ("open_application", "open_settings_page", "open_website",
                    "focus_window", "open_folder", "open_file")
# Phrases that mean the plan is handing the work back to the user.
_MANUAL_HANDOFF = ("manually", "you can then", "where you can", "so you can",
                   "the user can", "yourself", "by hand")


def plan_shortfall(goal: str, steps) -> str:
    """Return why a plan fails to reach `goal`, or "" when it looks complete.

    Pure and deterministic, so it is unit-testable without an LLM or a desktop.
    """
    text = _clean(goal, 400).lower()
    if not steps:
        return "no steps"
    if _CHANGE_VERB_RE is None:
        return ""

    tools = [str(getattr(s, "tool", "")).strip() for s in steps]
    descriptions = " ".join(str(getattr(s, "description", "")) for s in steps).lower()

    for phrase in _MANUAL_HANDOFF:
        if phrase in descriptions:
            return f"a step tells the user to do it themselves ('{phrase}')"

    # Word boundaries matter: a plain substring test made "display settings"
    # look like a change request, because "display" contains "play".
    wants_change = bool(_CHANGE_VERB_RE.search(text))
    if wants_change and tools and all(t in _OPEN_ONLY_TOOLS for t in tools):
        return ("the goal asks for a change but the plan only opens things "
                f"({', '.join(tools)})")
    return ""


def _tool_catalogue(registry, limit: int = 60) -> str:
    """Compact 'name: description (params)' listing for the prompt."""
    lines = []
    try:
        schemas = registry.openai_schemas()
    except Exception:  # noqa: BLE001
        schemas = []
    for s in schemas[:limit]:
        fn = s.get("function", s) if isinstance(s, dict) else {}
        name = fn.get("name", "")
        desc = _clean(fn.get("description", ""), 90)
        params = fn.get("parameters", {}).get("properties", {}) or {}
        pnames = ",".join(list(params.keys())[:6])
        if name:
            lines.append(f"- {name}({pnames}): {desc}")
    return "\n".join(lines)


def _extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    # Strip code fences and grab the first {...} block.
    t = text.strip()
    t = re.sub(r"^```(json)?|```$", "", t, flags=re.IGNORECASE | re.MULTILINE).strip()
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(t[start:end + 1])
    except Exception:  # noqa: BLE001
        return None


class Planner:
    """Decompose a command into a verifiable, tool-mapped Plan."""

    def __init__(self, llm_fn: Optional[Callable[[str, str], str]] = None,
                 max_steps: Optional[int] = None) -> None:
        # llm_fn(system_prompt, user_prompt) -> raw text ("" on any failure).
        self._llm = llm_fn
        self._max = int(max_steps if max_steps is not None else _MAX_STEPS)

    def make_plan(self, goal: str, registry, state: Optional[dict] = None) -> Plan:
        goal = (goal or "").strip()
        if not goal:
            return Plan(goal=goal, steps=[], source="fallback")
        llm = self._llm or _default_llm_fn
        catalogue = _tool_catalogue(registry)
        user = f"Available tools:\n{catalogue}\n\nCommand: {goal}"
        if state:
            user += f"\n\nCurrent state (for precondition skips): {_clean(json.dumps(state), 300)}"
        try:
            raw = llm(_SYSTEM_PROMPT, user) or ""
        except Exception as e:  # noqa: BLE001
            logger.warning("[PLANNER] llm failed: %s", _clean(e))
            return Plan(goal=goal, steps=[], source="fallback")
        data = _extract_json(raw)
        if not data or not isinstance(data.get("steps"), list):
            logger.info("[PLANNER] no usable plan for '%s' -> fallback to agent", _clean(goal))
            return Plan(goal=goal, steps=[], source="fallback")
        steps = self._build_steps(data["steps"], registry)
        if not steps:
            logger.info("[PLANNER] plan had no valid tool steps -> fallback to agent")
            return Plan(goal=goal, steps=[], source="fallback")
        shortfall = plan_shortfall(goal, steps)
        if shortfall:
            # A plan that stops short is worse than no plan: its one trivial step
            # verifies PASS, the run reports success, and the wrong behaviour gets
            # promoted into the command cache. Observed for real: "turn off night
            # light" was cached as "open Settings".
            logger.info("[PLANNER] rejecting plan for '%s': %s", _clean(goal), shortfall)
            dbg.info("PLANNER", "PLAN REJECTED", {"goal": goal[:120], "reason": shortfall,
                                                  "steps": [s.tool for s in steps]})
            return Plan(goal=goal, steps=[], source="fallback")
        logger.info("[PLANNER] '%s' -> %d step(s): %s", _clean(goal), len(steps),
                    " | ".join(_clean(s.tool, 24) for s in steps))
        return Plan(goal=goal, steps=steps, source="llm")

    # -- helpers -------------------------------------------------------- #
    def _build_steps(self, raw_steps: List[dict], registry) -> List[Step]:
        try:
            valid_names = set(registry.names())
        except Exception:  # noqa: BLE001
            valid_names = set()
        out: List[Step] = []
        for i, rs in enumerate(raw_steps):
            if not isinstance(rs, dict):
                continue
            tool = str(rs.get("tool", "")).strip()
            if not tool or (valid_names and tool not in valid_names):
                logger.info("[PLANNER] dropping unknown tool '%s'", _clean(tool))
                continue
            args = rs.get("args") or {}
            if not isinstance(args, dict):
                args = {}
            risky = bool(rs.get("risky", False))
            try:
                if registry.is_dangerous(tool):
                    risky = True
            except Exception:  # noqa: BLE001
                pass
            out.append(Step(
                description=_clean(rs.get("description") or tool, 120),
                tool=tool, args=args, risky=risky,
                precondition=_clean_ref(rs.get("precondition"), valid_names),
                fallback=_clean_ref(rs.get("fallback"), valid_names),
                id=i + 1,
            ))
            if len(out) >= self._max:
                logger.info("[PLANNER] capped plan at %d steps", self._max)
                break
        return out


def _clean_ref(ref: Any, valid_names: set) -> Optional[Dict[str, Any]]:
    """Validate an optional {tool,args} reference (precondition/fallback)."""
    if not isinstance(ref, dict):
        return None
    tool = str(ref.get("tool", "")).strip()
    if not tool or (valid_names and tool not in valid_names):
        return None
    args = ref.get("args") or {}
    if not isinstance(args, dict):
        args = {}
    return {"tool": tool, "args": args}


def _default_llm_fn(system_prompt: str, user_prompt: str) -> str:
    """Best-effort planner LLM using the shared provider pool.

    Fail-soft: any error (no keys, network off, rate-limit) returns "" so the
    Planner cleanly falls back to the normal agent loop.
    """
    try:
        from app.services import llm_providers as P
    except Exception:  # noqa: BLE001
        return ""
    # make_raw_client() is pointed at the Gemini OpenAI-compatible endpoint, so
    # the model MUST be a Gemini model name -- NOT AGENT_MODEL (a Groq model
    # name), which would 404 on every call and silently kill the planner.
    model = getattr(_cfg, "GEMINI_MODEL", None) or "gemini-2.0-flash"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    try:
        keys = P.ordered_keys()
    except Exception:  # noqa: BLE001
        keys = [0]
    for ki in (keys or [0])[:3]:
        try:
            client = P.make_raw_client(ki, timeout=getattr(_cfg, "AGENT_REQUEST_TIMEOUT", 18))
            resp = client.chat.completions.create(
                model=model, messages=messages, temperature=0.1,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:  # noqa: BLE001
            if P.is_rate_limit_error(e):
                try:
                    P.trip(ki)
                except Exception:  # noqa: BLE001
                    pass
                continue
            logger.debug("[PLANNER] default llm key %s failed: %s", ki, _clean(e))
            continue
    return ""
