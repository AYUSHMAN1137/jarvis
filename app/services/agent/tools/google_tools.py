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
    verification={"family": "query", "cacheable": False},
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
    verification={"family": "query", "cacheable": False},
)
def gmail_unread() -> str:
    if not deps.gmail_service:
        return "ERROR: Gmail is not configured."
    try:
        return deps.gmail_service.get_unread_summary()
    except Exception as e:  # noqa: BLE001
        return f"I couldn't read unread emails right now: {e}"


@tool(
    name="calendar_update",
    description=(
        "Change an existing calendar event's title and/or time. Use for "
        "'reschedule the dentist to 5pm', 'rename that meeting'. To create use "
        "calendar_create, to remove use calendar_delete."
    ),
    params={
        "query": {"type": "string",
                  "description": "Which event to change, e.g. 'dentist appointment'."},
        "new_title": {"type": "string", "required": False,
                      "description": "New title for the event."},
        "new_time": {"type": "string", "required": False,
                     "description": "New time in natural language, e.g. 'tomorrow at 4 pm'."},
    },
    category="google",
    verification={"family": "google"},
)
def calendar_update(query: str, new_title: str = "", new_time: str = "") -> str:
    if not deps.calendar_service:
        return "ERROR: Google Calendar is not configured."
    try:
        return deps.calendar_service.update_event_from_text(
            query, new_title=new_title, new_time=new_time)
    except Exception as e:  # noqa: BLE001
        return f"ERROR: could not update that event: {e}"


@tool(
    name="drive_download",
    description=(
        "Download a file from the user's Google Drive to this computer. "
        "Google Docs/Sheets/Slides are exported (PDF or XLSX) since they have no "
        "raw form. Saves to Downloads unless another folder is given."
    ),
    params={
        "name": {"type": "string", "description": "File name, or part of it, to download."},
        "destination": {"type": "string", "required": False,
                        "description": "Local folder to save into. Defaults to Downloads."},
    },
    category="google",
    verification={"family": "file"},
)
def drive_download(name: str, destination: str = "") -> str:
    if not deps.drive_service:
        return "ERROR: Google Drive is not configured."
    try:
        return deps.drive_service.download_to_path(name, destination=destination)
    except (ValueError, FileNotFoundError) as e:
        return f"ERROR: {e}"
    except Exception as e:  # noqa: BLE001
        return f"ERROR: could not download from Drive: {e}"


@tool(
    name="gmail_send",
    description=(
        "Send an email from the user's Gmail account. Marked dangerous because "
        "a sent message cannot be recalled -- always show the user the "
        "recipient, subject and body and get agreement before calling this."
    ),
    params={
        "to": {"type": "string",
               "description": "Recipient address. Several may be separated by commas."},
        "subject": {"type": "string", "description": "Subject line."},
        "body": {"type": "string", "description": "Plain-text message body."},
        "cc": {"type": "string", "required": False, "description": "Optional CC addresses."},
    },
    dangerous=True,
    category="google",
    verification={"family": "google"},
)
def gmail_send(to: str, subject: str, body: str, cc: str = "") -> str:
    if not deps.gmail_service:
        return "ERROR: Gmail is not configured."
    try:
        return deps.gmail_service.send_message(to, subject, body, cc=cc)
    except ValueError as e:
        return f"ERROR: {e}"
    except Exception as e:  # noqa: BLE001
        message = str(e)
        if "insufficient" in message.lower() or "scope" in message.lower():
            return ("ERROR: the saved Google login does not include permission "
                    "to send mail. Delete data/google_token.json and sign in "
                    "again to grant it.")
        return f"ERROR: could not send the email: {message}"


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
    verification={"family": "query", "cacheable": False},
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
    verification={"family": "query", "cacheable": False},
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
    verification={"family": "google"},
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
    verification={"family": "google"},
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
    verification={"family": "query", "cacheable": False},
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
    verification={"family": "query", "cacheable": False},
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
    verification={"family": "google"},
)
def drive_upload(request: str) -> str:
    if not deps.drive_service:
        return "ERROR: Drive is not configured."
    try:
        return deps.drive_service.upload_from_text((request or "").strip())
    except Exception as e:  # noqa: BLE001
        return f"I couldn't upload that file to Google Drive right now: {e}"
