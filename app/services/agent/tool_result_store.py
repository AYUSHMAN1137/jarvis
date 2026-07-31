"""Keep oversized tool results out of the conversation.

The agent loop appends every tool result straight into `messages`, and those
messages are re-sent to the model on each of the up-to-16 steps that follow. A
single `ui_list_controls` can return UIA_MAX_NODES (4000) nodes, so one call
could dominate the prompt for the rest of the turn -- measured latency showed a
p50 of 1.65s but a p90 of 19.65s and a worst case of 100s.

So: anything over the threshold is written to disk and replaced in the
conversation by a short pointer. The agent can `read_file` the path when it
genuinely needs the detail, which is rare -- usually it only needed the first
few lines to pick a control.

Size-based rather than a list of "bulky tools" on purpose: a list would need
updating every time a tool is added, and the project rule is to integrate
through metadata, never hardcoded tool lists.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("J.A.R.V.I.S")


def _cfg(name: str, default: Any) -> Any:
    try:
        import config as _config
        return getattr(_config, name, default)
    except Exception:  # noqa: BLE001
        return default


_BASE_DIR = Path(_cfg("BASE_DIR", Path(__file__).resolve().parent.parent.parent.parent))
TOOL_RESULT_DIR = _BASE_DIR / "data" / "tool_results"

# read_file is how the agent gets an offloaded result back. Offloading its own
# output would create a persist -> read -> persist loop.
_EXEMPT_TOOLS = frozenset({"read_file"})

_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


def threshold() -> int:
    try:
        return int(_cfg("AGENT_TOOL_RESULT_MAX_CHARS", 4000))
    except (TypeError, ValueError):
        return 4000


def should_offload(tool: str, observation: str) -> bool:
    limit = threshold()
    if limit <= 0 or not observation:
        return False
    if (tool or "") in _EXEMPT_TOOLS:
        return False
    # An error message is short and the agent needs it verbatim to recover.
    if str(observation).startswith("ERROR"):
        return False
    return len(observation) > limit


def offload(tool: str, observation: str, execution_id: str = "",
            action_id: str = "") -> Optional[str]:
    """Write the result to disk and return the path, or None if it could not be.

    Failing to write is not an error worth surfacing: the caller simply keeps
    the full text inline, exactly as before.
    """
    try:
        folder = TOOL_RESULT_DIR / (_SAFE.sub("_", str(execution_id)) or "adhoc")
        folder.mkdir(parents=True, exist_ok=True)
        stem = _SAFE.sub("_", f"{action_id or 'result'}_{tool or 'tool'}")[:80]
        path = folder / f"{stem}.txt"
        path.write_text(observation, encoding="utf-8", errors="replace")
        return str(path)
    except OSError as exc:
        logger.debug("[TOOL-RESULT] could not offload %s: %s", tool, exc)
        return None


def summarize(tool: str, observation: str, path: str, head_lines: int = 12) -> str:
    """The pointer that replaces the full result in the conversation.

    Keeps the first few lines: for a control listing or a directory listing that
    is usually the whole answer, so the agent can often continue without a
    second read at all.
    """
    lines = observation.splitlines()
    head = "\n".join(lines[:head_lines]).strip()
    remaining = max(0, len(lines) - head_lines)
    parts = [
        f"[{len(observation):,} chars from {tool}; showing the first "
        f"{min(head_lines, len(lines))} of {len(lines)} lines]",
    ]
    if head:
        parts.append(head)
    if remaining:
        parts.append(f"... {remaining:,} more line(s) omitted.")
    parts.append(f"Full output saved to: {path}\n"
                 f"Use read_file with that exact path if you need the rest.")
    return "\n".join(parts)


def maybe_offload(tool: str, observation: str, execution_id: str = "",
                  action_id: str = "") -> str:
    """Return what should go into the conversation for this tool result."""
    if not should_offload(tool, observation):
        return observation
    path = offload(tool, observation, execution_id, action_id)
    if not path:
        return observation
    compact = summarize(tool, observation, path)
    logger.info("[TOOL-RESULT] offloaded %s (%d chars -> %d) to %s",
                tool, len(observation), len(compact), os.path.basename(path))
    return compact
