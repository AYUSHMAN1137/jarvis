"""
Google Workspace tools: Gmail, Calendar, Drive.

Thin wrappers over the existing skill services (unchanged). Each returns a
text summary that is both fed back to the LLM and shown to the user.
"""

from __future__ import annotations

import logging

from app.services.agent.tool_registry import tool
from app.services.agent.deps import deps

logger = logging.getLogger("J.A.R.V.I.S")


# ---- Gmail ------------------------------------------------------------- #
@tool(
    name="gmail_inbox",
    description="Read a summary of the latest emails in the user's Gmail inbox.",
    params={},
    category="google",
)
def gmail_inbox() -> str:
    if not deps.gmail_service:
        return "ERROR: Gmail is not configured."
    try:
        return deps.gmail_service.get_inbox_summary()
    except Exception as e:  # noqa: BLE001
        return f"I couldn't open the Gmail inbox right now: {e}"


@tool(
    name="gmail_unread",
    description="Read a summary of the user's unread Gmail emails.",
    params={},
    category="google",
)
def gmail_unread() -> str:
    if not deps.gmail_service:
        return "ERROR: Gmail is not configured."
    try:
        return deps.gmail_service.get_unread_summary()
    except Exception as e:  # noqa: BLE001
        return f"I couldn't read unread emails right now: {e}"


# ---- Calendar ---------------------------------------------------------- #
@tool(
    name="calendar_list",
    description="List today's or upcoming events from the user's Google Calendar.",
    params={
        "scope": {
            "type": "string",
            "description": "Which events to list.",
            "required": False,
            "enum": ["today", "upcoming"],
        }
    },
    category="google",
)
def calendar_list(scope: str = "upcoming") -> str:
    if not deps.calendar_service:
        return "ERROR: Calendar is not configured."
    try:
        if (scope or "").strip().lower() == "today":
            return deps.calendar_service.get_today_events_summary()
        return deps.calendar_service.get_upcoming_events_summary()
    except Exception as e:  # noqa: BLE001
        return f"I couldn't read your calendar right now: {e}"


@tool(
    name="calendar_search",
    description="Search the user's Google Calendar for a specific event or reminder.",
    params={"query": {"type": "string", "description": "Event to search for."}},
    category="google",
)
def calendar_search(query: str) -> str:
    if not deps.calendar_service:
        return "ERROR: Calendar is not configured."
    try:
        return deps.calendar_service.search_events_summary((query or "").strip())
    except Exception as e:  # noqa: BLE001
        return f"I couldn't search your calendar right now: {e}"


@tool(
    name="calendar_create",
    description=(
        "Create a new Google Calendar event or reminder from a natural-language "
        "description, e.g. 'meeting tomorrow at 5 pm'."
    ),
    params={"details": {"type": "string", "description": "Event details in plain language."}},
    category="google",
)
def calendar_create(details: str) -> str:
    if not deps.calendar_service:
        return "ERROR: Calendar is not configured."
    try:
        return deps.calendar_service.create_event_from_text((details or "").strip())
    except Exception as e:  # noqa: BLE001
        return f"I couldn't create that calendar event right now: {e}"


@tool(
    name="calendar_delete",
    description="Delete / cancel an event from the user's Google Calendar.",
    params={"query": {"type": "string", "description": "Which event to delete."}},
    dangerous=True,
    category="google",
)
def calendar_delete(query: str) -> str:
    if not deps.calendar_service:
        return "ERROR: Calendar is not configured."
    try:
        return deps.calendar_service.delete_event_from_text((query or "").strip())
    except Exception as e:  # noqa: BLE001
        return f"I couldn't delete that calendar event right now: {e}"


# ---- Drive ------------------------------------------------------------- #
@tool(
    name="drive_search",
    description="Search the user's Google Drive for files by name.",
    params={"query": {"type": "string", "description": "File name or keyword to search."}},
    category="google",
)
def drive_search(query: str) -> str:
    if not deps.drive_service:
        return "ERROR: Drive is not configured."
    try:
        return deps.drive_service.search_files_summary((query or "").strip())
    except Exception as e:  # noqa: BLE001
        return f"I couldn't search Google Drive right now: {e}"


@tool(
    name="drive_list",
    description="List files/folders in the user's Google Drive root or a named folder.",
    params={"query": {"type": "string", "description": "Optional folder name.", "required": False}},
    category="google",
)
def drive_list(query: str = "") -> str:
    if not deps.drive_service:
        return "ERROR: Drive is not configured."
    try:
        return deps.drive_service.list_items_summary((query or "").strip())
    except Exception as e:  # noqa: BLE001
        return f"I couldn't list your Google Drive items right now: {e}"


@tool(
    name="drive_upload",
    description=(
        "Upload a local file to Google Drive. Provide the request including the "
        "local file path and optionally a target folder."
    ),
    params={"request": {"type": "string", "description": "Upload request, e.g. 'C:\\\\file.pdf to folder Jobs'."}},
    category="google",
)
def drive_upload(request: str) -> str:
    if not deps.drive_service:
        return "ERROR: Drive is not configured."
    try:
        return deps.drive_service.upload_from_text((request or "").strip())
    except Exception as e:  # noqa: BLE001
        return f"I couldn't upload that file to Google Drive right now: {e}"
