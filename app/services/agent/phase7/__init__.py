"""Phase 7 -- Proactive assistance (suggest-only by default).

Exposes the pure state-diff helper and the ProactiveEngine that turns system
events + learned habits into user-facing suggestions.
"""

from app.services.agent.phase7.events import (
    diff_state,
    ALL_EVENTS,
    EVT_APP_OPENED,
    EVT_APP_CLOSED,
    EVT_WINDOW_FOCUSED,
    EVT_CLIPBOARD_CHANGED,
    EVT_SETTINGS_CHANGED,
)
from app.services.agent.phase7.proactive_engine import (
    ProactiveEngine,
    get_phase7,
    CONSENT_ASK,
    CONSENT_ALLOW,
    CONSENT_DENY,
    VALID_CONSENT,
)

__all__ = [
    "diff_state",
    "ALL_EVENTS",
    "EVT_APP_OPENED",
    "EVT_APP_CLOSED",
    "EVT_WINDOW_FOCUSED",
    "EVT_CLIPBOARD_CHANGED",
    "EVT_SETTINGS_CHANGED",
    "ProactiveEngine",
    "get_phase7",
    "CONSENT_ASK",
    "CONSENT_ALLOW",
    "CONSENT_DENY",
    "VALID_CONSENT",
]
