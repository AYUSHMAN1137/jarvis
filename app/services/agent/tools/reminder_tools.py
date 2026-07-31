"""Reminder tools — let the agent create, list, cancel and snooze reminders.

Uses the ReminderService singleton for all DB work. Time parsing is done by
the LLM itself (the tool receives natural-language ``when`` and the brain
converts it to ISO 8601 before calling the tool).

Registered into the shared tool registry via tools/__init__.py.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from app.services.agent.tool_registry import tool
from app.services.agent import action_sink

logger = logging.getLogger("J.A.R.V.I.S")

IST = timezone(timedelta(hours=5, minutes=30))


def _get_svc():
    from app.services.reminder_service import get_reminder_service
    return get_reminder_service()


def _friendly_time(iso_str: str) -> str:
    """Convert ISO 8601 to a friendly string like 'Tomorrow at 9:00 AM'."""
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        now = datetime.now(tz=IST)

        today = now.date()
        reminder_date = dt.date()
        diff_days = (reminder_date - today).days

        if diff_days == 0:
            date_part = "Today"
        elif diff_days == 1:
            date_part = "Tomorrow"
        elif diff_days == -1:
            date_part = "Yesterday"
        elif 2 <= diff_days <= 6:
            date_part = dt.strftime("%A")  # "Monday", "Tuesday", etc.
        else:
            date_part = dt.strftime("%d %b")  # "30 Jul"

        time_part = dt.strftime("%I:%M %p").lstrip("0")  # "9:00 AM"
        return f"{date_part} at {time_part}"
    except Exception:
        return iso_str


@tool(
    name="set_reminder",
    description=(
        "Set a reminder for the user. The user might say things like "
        "'remind me to call mom at 5 PM', 'kal subah 8 baje exercise ka reminder', "
        "'remind me every Monday at 9 AM to send weekly report', "
        "'30 minute mein yaad dila dena meeting ke baare mein'. "
        "You MUST convert the user's natural language time into an ISO 8601 "
        "datetime string (with timezone +05:30 for IST) for the 'due_at' parameter. "
        "Use the current time context to resolve relative times like 'tomorrow', "
        "'in 30 minutes', 'next Monday', etc. "
        "IMPORTANT: The due_at MUST be in the future. Never set a past time. "
        "For vague words: morning=09:00, afternoon=13:00, evening=18:00, night=21:00."
    ),
    params={
        "title": {
            "type": "string",
            "description": "Short title for the reminder, e.g. 'Call mom', 'Exercise', 'Send report'.",
            "required": True,
        },
        "due_at": {
            "type": "string",
            "description": (
                "ISO 8601 datetime string with timezone for when the reminder should fire. "
                "Example: '2026-07-30T17:00:00+05:30'. You must calculate this from the "
                "user's natural language time expression. MUST be in the future."
            ),
            "required": True,
        },
        "recurrence": {
            "type": "string",
            "description": "How often the reminder repeats. Leave empty for one-time reminders.",
            "required": False,
            "enum": ["daily", "weekdays", "weekly", "monthly"],
        },
        "description": {
            "type": "string",
            "description": "Optional longer description for the reminder.",
            "required": False,
        },
    },
    category="system",
    verification={"family": "memory"},
)
def set_reminder(title: str, due_at: str, recurrence: str = None,
                 description: str = "") -> str:
    svc = _get_svc()

    # ── Validate due_at is in the future ──
    try:
        due_dt = datetime.fromisoformat(due_at)
        if due_dt.tzinfo is None:
            due_dt = due_dt.replace(tzinfo=IST)
        now = datetime.now(tz=IST)
        if due_dt <= now:
            # If it's a recurring reminder set for a past time today, advance to tomorrow
            if recurrence:
                due_dt += timedelta(days=1)
                due_at = due_dt.isoformat()
                logger.info("[REMINDERS] Adjusted past-due recurring reminder to: %s", due_at)
            else:
                return (
                    f"ERROR: The time '{_friendly_time(due_at)}' is in the past. "
                    f"Please set a future time for the reminder."
                )
    except (ValueError, TypeError) as e:
        return f"ERROR: Invalid date/time format: {due_at}. Use ISO 8601 (e.g. '2026-07-30T17:00:00+05:30')."

    reminder = svc.add(title=title, due_at=due_at, recurrence=recurrence,
                       description=description)
    # Push action to open the reminders panel
    action_sink.set_panel("reminders", {"action": "open"})

    friendly = _friendly_time(reminder['due_at'])
    recur_text = f" (repeating {recurrence})" if recurrence else ""
    return (
        f"✅ Reminder set: '{reminder['title']}' — {friendly}"
        f"{recur_text}. I'll notify you when it's time."
    )


@tool(
    name="list_reminders",
    description=(
        "Show the user's active reminders. Use when the user says things like "
        "'show my reminders', 'mere reminders dikha do', 'what reminders do I have', "
        "'kya kya yaad dilana hai'. Opens a popup panel showing all reminders."
    ),
    params={
        "filter": {
            "type": "string",
            "description": "Filter type for reminders.",
            "required": False,
            "enum": ["all", "today", "upcoming"],
        },
    },
    category="system",
    verification={"family": "query", "cacheable": False},
)
def list_reminders(filter: str = "all") -> str:
    svc = _get_svc()
    reminders = svc.list_active(filter_type=filter)

    # Push action to open the reminders panel
    action_sink.set_panel("reminders", {"action": "open"})

    if not reminders:
        return "You don't have any active reminders right now."

    lines = [f"You have {len(reminders)} active reminder(s):\n"]
    for r in reminders:
        recur = f" 🔁 {r['recurrence']}" if r.get("recurrence") else ""
        friendly = _friendly_time(r['due_at'])
        lines.append(f"• {r['title']} — {friendly}{recur}")

    return "\n".join(lines)


@tool(
    name="cancel_reminder",
    description=(
        "Cancel/delete a reminder. Use when the user says things like "
        "'cancel my exercise reminder', 'delete the meeting reminder', "
        "'exercise wala reminder hata do', 'reminder cancel kar do'."
    ),
    params={
        "query": {
            "type": "string",
            "description": "Search text to match against reminder titles.",
            "required": True,
        },
    },
    category="system",
    verification={"family": "memory"},
)
def cancel_reminder(query: str) -> str:
    svc = _get_svc()
    count = svc.cancel(query)

    # Refresh the panel
    action_sink.set_panel("reminders", {"action": "refresh"})

    if count:
        return f"Cancelled {count} reminder(s) matching '{query}'."
    return f"I couldn't find any active reminder matching '{query}'."


@tool(
    name="snooze_reminder",
    description=(
        "Snooze the most recently fired reminder. Use when the user says "
        "'snooze for 10 minutes', 'snooze kar do', 'baad mein yaad dilana', "
        "'5 minute baad phir bata dena'."
    ),
    params={
        "minutes": {
            "type": "integer",
            "description": "How many minutes to snooze for.",
            "required": False,
        },
    },
    category="system",
    verification={"family": "memory"},
)
def snooze_reminder(minutes: int = 10) -> str:
    svc = _get_svc()
    result = svc.snooze(minutes=int(minutes))

    action_sink.set_panel("reminders", {"action": "refresh"})

    if result:
        return f"Snoozed '{result['title']}' for {minutes} minutes. I'll remind you again."
    return "There's no recently fired reminder to snooze."
