"""Phase 8 -- Personalization (user model).

Exposes the UserModel (facts + aliases + learned habits) and its singleton.
The habit lookup also serves as Phase 7's habit provider.
"""

from app.services.agent.personalization.user_model import (
    UserModel,
    get_phase8,
)

__all__ = ["UserModel", "get_phase8"]
