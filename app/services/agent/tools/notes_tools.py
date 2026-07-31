"""Notes & To-Do tools — let the agent manage notes and to-do lists.

Consolidated into fewer tools (6 instead of 11) to keep the tool count manageable:
  * ``notes_manage`` — create / edit / delete / show notes (4-in-1)
  * ``todo_manage`` — create_list / delete_list / show (3-in-1)
  * ``todo_item`` — add / remove / mark_done / mark_undone items (4-in-1)

Each tool pushes a ``notes_panel`` action via the action_sink so the frontend
popup opens or refreshes automatically.

Registered via tools/__init__.py.
"""

from __future__ import annotations

import json
import logging

from app.services.agent.tool_registry import tool
from app.services.agent import action_sink

logger = logging.getLogger("J.A.R.V.I.S")


def _get_svc():
    from app.services.notes_service import get_notes_service
    return get_notes_service()


# ═══════════════════════════════════════════════════════════════════════
# NOTES TOOL (4-in-1)
# ═══════════════════════════════════════════════════════════════════════

@tool(
    name="notes_manage",
    description=(
        "Manage the user's notes. Supports actions: create, edit, delete, show. "
        "Use when the user says things like "
        "'create a note about birthday ideas', 'note this down', 'ye likh lo', "
        "'edit my birthday note', 'delete that note', 'notes dikha do', "
        "'show my notes', 'mere notes dikhao'.\n\n"
        "Action guide:\n"
        "• create — make a new note with a title and optional body\n"
        "• edit — modify an existing note (replace body or append to it)\n"
        "• delete — remove a note\n"
        "• show — display all notes (opens the notes popup panel)"
    ),
    params={
        "action": {
            "type": "string",
            "description": "What to do with the note.",
            "required": True,
            "enum": ["create", "edit", "delete", "show"],
        },
        "title": {
            "type": "string",
            "description": (
                "For 'create': the title of the new note. "
                "For 'edit'/'delete': search text to find the note by title."
            ),
            "required": True,
        },
        "body": {
            "type": "string",
            "description": (
                "For 'create': initial body text (markdown). "
                "For 'edit': if provided, REPLACES the entire body."
            ),
            "required": False,
        },
        "append": {
            "type": "string",
            "description": "For 'edit': text to APPEND to the existing body (used instead of 'body' for additions).",
            "required": False,
        },
        "search": {
            "type": "string",
            "description": "For 'show': optional search/filter text to narrow results.",
            "required": False,
        },
    },
    category="system",
    verification={"family": "memory"},
)
def notes_manage(action: str, title: str = "", body: str = None,
                 append: str = None, search: str = None) -> str:
    svc = _get_svc()
    action = action.lower().strip()

    if action == "create":
        if not title:
            return "I need a title to create a note."
        note = svc.create_note(title=title, body=body or "")
        action_sink.set_panel("notes", {"action": "open", "tab": "notes"})
        return f"📝 Created note: '{note['title']}'."

    elif action == "edit":
        if not title:
            return "I need to know which note to edit. Tell me the title."
        result = svc.edit_note(query=title, body=body, append=append)
        if not result:
            return f"I couldn't find a note matching '{title}'."
        action_sink.set_panel("notes", {"action": "open", "tab": "notes"})
        return f"✏️ Updated note: '{result['title']}'."

    elif action == "delete":
        if not title:
            return "I need to know which note to delete."
        count = svc.delete_note(query=title)
        action_sink.set_panel("notes", {"action": "refresh", "tab": "notes"})
        if count:
            return f"🗑️ Deleted {count} note(s) matching '{title}'."
        return f"I couldn't find a note matching '{title}'."

    elif action == "show":
        notes = svc.list_notes(query=search or title if title else None)
        action_sink.set_panel("notes", {"action": "open", "tab": "notes"})
        if not notes:
            return "You don't have any notes yet."
        lines = [f"You have {len(notes)} note(s):\n"]
        for n in notes:
            pin = "📌 " if n.get("pinned") else ""
            preview = (n.get("markdown_body") or "")[:60]
            if len(n.get("markdown_body", "")) > 60:
                preview += "..."
            lines.append(f"• {pin}{n['title']}: {preview}")
        return "\n".join(lines)

    return f"Unknown note action: '{action}'. Use create, edit, delete, or show."


# ═══════════════════════════════════════════════════════════════════════
# TO-DO LIST TOOL (3-in-1)
# ═══════════════════════════════════════════════════════════════════════

@tool(
    name="todo_manage",
    description=(
        "Manage the user's to-do lists. Supports actions: create, delete, show. "
        "Use when the user says things like "
        "'create a shopping list', 'shopping list banao', "
        "'delete my shopping list', 'shopping list hata do', "
        "'show my to-do lists', 'to-do dikha do', 'what's on my list'.\n\n"
        "Action guide:\n"
        "• create — make a new to-do list (optionally with initial items)\n"
        "• delete — remove an entire to-do list\n"
        "• show — display all to-do lists (opens the to-do popup panel)"
    ),
    params={
        "action": {
            "type": "string",
            "description": "What to do.",
            "required": True,
            "enum": ["create", "delete", "show"],
        },
        "list_name": {
            "type": "string",
            "description": "Name of the to-do list to create, delete, or show.",
            "required": True,
        },
        "items": {
            "type": "string",
            "description": (
                "For 'create': comma-separated initial items to add. "
                "Example: 'milk, eggs, bread'."
            ),
            "required": False,
        },
    },
    category="system",
    verification={"family": "memory"},
)
def todo_manage(action: str, list_name: str = "", items: str = "") -> str:
    svc = _get_svc()
    action = action.lower().strip()

    if action == "create":
        if not list_name:
            return "I need a name for the to-do list."
        item_list = [i.strip() for i in items.split(",") if i.strip()] if items else []
        lst = svc.create_todo_list(title=list_name, items=item_list)
        action_sink.set_panel("notes", {"action": "open", "tab": "todo"})
        item_text = f" with {len(item_list)} item(s)" if item_list else ""
        return f"✅ Created to-do list: '{lst['title']}'{item_text}."

    elif action == "delete":
        if not list_name:
            return "I need to know which list to delete."
        count = svc.delete_todo_list(name=list_name)
        action_sink.set_panel("notes", {"action": "refresh", "tab": "todo"})
        if count:
            return f"🗑️ Deleted {count} to-do list(s) matching '{list_name}'."
        return f"I couldn't find a to-do list matching '{list_name}'."

    elif action == "show":
        if list_name:
            lst = svc.find_todo_list(list_name)
            if lst:
                action_sink.set_panel("notes", {"action": "open", "tab": "todo"})
                items_text = []
                for item in lst.get("items", []):
                    check = "☑" if item["done"] else "☐"
                    items_text.append(f"  {check} {item['text']}")
                return f"📋 {lst['title']}:\n" + "\n".join(items_text) if items_text else f"📋 {lst['title']}: (empty)"
            return f"I couldn't find a to-do list matching '{list_name}'."

        lists = svc.list_todo_lists()
        action_sink.set_panel("notes", {"action": "open", "tab": "todo"})
        if not lists:
            return "You don't have any to-do lists yet."
        lines = [f"You have {len(lists)} to-do list(s):\n"]
        for lst in lists:
            total = len(lst.get("items", []))
            done = sum(1 for i in lst.get("items", []) if i["done"])
            lines.append(f"• {lst['title']} — {done}/{total} done")
        return "\n".join(lines)

    return f"Unknown to-do action: '{action}'. Use create, delete, or show."


# ═══════════════════════════════════════════════════════════════════════
# TO-DO ITEM TOOL (4-in-1)
# ═══════════════════════════════════════════════════════════════════════

@tool(
    name="todo_item",
    description=(
        "Manage items within a to-do list. Supports actions: add, remove, done, undone. "
        "Use when the user says things like "
        "'add milk to shopping list', 'shopping list mein cheese add kar do', "
        "'remove eggs from shopping list', 'eggs hata do shopping list se', "
        "'mark buy milk as done', 'milk ho gaya', "
        "'uncheck bread', 'bread abhi nahi hua'.\n\n"
        "Action guide:\n"
        "• add — add item(s) to a list (creates the list if it doesn't exist)\n"
        "• remove — remove matching item(s) from a list\n"
        "• done — mark matching item(s) as completed\n"
        "• undone — mark matching item(s) as not completed"
    ),
    params={
        "action": {
            "type": "string",
            "description": "What to do with the item(s).",
            "required": True,
            "enum": ["add", "remove", "done", "undone"],
        },
        "list_name": {
            "type": "string",
            "description": "Name of the to-do list.",
            "required": True,
        },
        "items": {
            "type": "string",
            "description": (
                "For 'add': comma-separated items to add. Example: 'cheese, butter'. "
                "For 'remove'/'done'/'undone': search text to match item(s). Example: 'milk'."
            ),
            "required": True,
        },
    },
    category="system",
    verification={"family": "memory"},
)
def todo_item(action: str, list_name: str, items: str) -> str:
    svc = _get_svc()
    action = action.lower().strip()

    if action == "add":
        item_list = [i.strip() for i in items.split(",") if i.strip()]
        if not item_list:
            return "I need at least one item to add."
        result = svc.add_todo_items(list_name=list_name, items=item_list)
        action_sink.set_panel("notes", {"action": "open", "tab": "todo"})
        if result:
            return f"➕ Added {len(item_list)} item(s) to '{result['title']}'."
        return f"Could not add items to '{list_name}'."

    elif action == "remove":
        result = svc.remove_todo_items(list_name=list_name, item_query=items)
        action_sink.set_panel("notes", {"action": "refresh", "tab": "todo"})
        if result:
            return f"🗑️ Removed item(s) matching '{items}' from '{result['title']}'."
        return f"I couldn't find a to-do list matching '{list_name}'."

    elif action == "done":
        result = svc.mark_todo_done(list_name=list_name, item_query=items)
        action_sink.set_panel("notes", {"action": "refresh", "tab": "todo"})
        if result:
            return f"☑️ Marked '{items}' as done in '{result['title']}'."
        return f"I couldn't find that item in '{list_name}'."

    elif action == "undone":
        result = svc.mark_todo_undone(list_name=list_name, item_query=items)
        action_sink.set_panel("notes", {"action": "refresh", "tab": "todo"})
        if result:
            return f"☐ Marked '{items}' as not done in '{result['title']}'."
        return f"I couldn't find that item in '{list_name}'."

    return f"Unknown to-do item action: '{action}'. Use add, remove, done, or undone."
