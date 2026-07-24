"""Phase 6 -- Fast local cache (verified-only).

Exposes the CommandCache store and the coordinator that promotes/evicts/looks
up cached commands. The coordinator imports its heavier dependencies lazily so
a missing piece never breaks startup.
"""

from app.services.agent.cache.command_cache import (
    CommandCache,
    KIND_TOOL,
    KIND_PLAN,
    KIND_RESPONSE,
    VALID_KINDS,
)
from app.services.agent.cache.coordinator import (
    Phase6Coordinator,
    get_phase6,
)

__all__ = [
    "CommandCache",
    "KIND_TOOL",
    "KIND_PLAN",
    "KIND_RESPONSE",
    "VALID_KINDS",
    "Phase6Coordinator",
    "get_phase6",
]
