"""Phase 4 -- Checker + Vision + Self-Learning.

Kept import-light on purpose: importing this package must NOT pull in heavy
optional deps (openai / groq / pyautogui / psutil). The coordinator and its
components are imported lazily via get_phase4().
"""

from __future__ import annotations

from app.services.agent.checker import models  # lightweight value types

__all__ = ["models", "get_phase4", "get_event_bus"]


def get_phase4():
    """Return the process-wide Phase 4 coordinator (lazy import)."""
    from app.services.agent.checker.coordinator import get_phase4 as _g
    return _g()


def get_event_bus():
    """Return the process-wide event bus (lazy import)."""
    from app.services.agent.checker.event_bus import get_event_bus as _g
    return _g()
