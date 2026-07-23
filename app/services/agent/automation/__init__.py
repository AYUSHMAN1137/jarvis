"""Phase 5 -- automation engines.

Kept import-light on purpose: importing this package must NOT pull in heavy
optional deps (pywinauto / comtypes). The UIA engine is imported lazily via
get_uia_engine() and only touches pywinauto the first time it is actually used.
"""

from __future__ import annotations

__all__ = ["get_uia_engine"]


def get_uia_engine():
    """Return the process-wide UIA engine (lazy import)."""
    from app.services.agent.automation.uia_engine import get_uia_engine as _g
    return _g()
