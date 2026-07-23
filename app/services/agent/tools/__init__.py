"""
Importing this package registers every built-in tool into the shared registry.

To add a NEW tool, either:
  - add a function with @tool(...) to one of the existing modules, or
  - create a new module here and import it below.

That's the only wiring needed — the agent loop picks it up automatically.
"""

from app.services.agent.tools import desktop_tools  # noqa: F401
from app.services.agent.tools import system_tools    # noqa: F401
from app.services.agent.tools import settings_tools  # noqa: F401
from app.services.agent.tools import file_tools       # noqa: F401
from app.services.agent.tools import web_tools         # noqa: F401
from app.services.agent.tools import content_tools     # noqa: F401
from app.services.agent.tools import google_tools      # noqa: F401
from app.services.agent.tools import memory_tools      # noqa: F401
from app.services.agent.tools import system_info_tools # noqa: F401
from app.services.agent.tools import uia_tools         # noqa: F401  (Phase 5)
from app.services.agent.tools import planner_tools     # noqa: F401  (Phase 5)


def load_all_tools() -> None:
    """No-op: importing this package already registered all tools.
    Provided as an explicit, readable call site during startup."""
    return None
