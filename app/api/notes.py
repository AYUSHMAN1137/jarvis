"""API routes for Notes & To-Do.

Endpoints:
  GET    /api/notes                — list all notes
  POST   /api/notes                — create a note
  PUT    /api/notes/{id}           — update a note
  DELETE /api/notes/{id}           — delete a note
  GET    /api/todos                — list all to-do lists
  POST   /api/todos                — create a to-do list
  DELETE /api/todos/{id}           — delete a to-do list
  POST   /api/todos/{list_id}/items       — add items
  PUT    /api/todos/items/{item_id}/done   — toggle done
  DELETE /api/todos/items/{item_id}        — delete an item
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger("J.A.R.V.I.S")

router = APIRouter(prefix="/api", tags=["notes"])


def _get_svc():
    from app.services.notes_service import get_notes_service
    return get_notes_service()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class NoteCreate(BaseModel):
    title: str
    body: str = ""


class NoteUpdate(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    append: Optional[str] = None
    pinned: Optional[bool] = None


class TodoListCreate(BaseModel):
    title: str
    items: List[str] = []


class TodoItemAdd(BaseModel):
    items: List[str]


class TodoItemDone(BaseModel):
    done: bool = True


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------
@router.get("/notes")
async def list_notes(search: str = None):
    svc = _get_svc()
    return {"notes": svc.list_notes(query=search)}


@router.post("/notes")
async def create_note(body: NoteCreate):
    svc = _get_svc()
    note = svc.create_note(title=body.title, body=body.body)
    return {"note": note}


@router.put("/notes/{note_id}")
async def update_note(note_id: int, body: NoteUpdate):
    svc = _get_svc()
    note = svc.get_note(note_id)
    if not note:
        return {"error": "Note not found"}

    # Build edit params
    if body.pinned is not None:
        svc.pin_note(query=note["title"], pinned=body.pinned)

    if body.body is not None or body.append is not None or body.title is not None:
        svc.edit_note(
            query=note["title"],
            body=body.body,
            append=body.append,
            new_title=body.title,
        )

    return {"note": svc.get_note(note_id)}


@router.delete("/notes/{note_id}")
async def delete_note(note_id: int):
    svc = _get_svc()
    note = svc.get_note(note_id)
    if not note:
        return {"error": "Note not found"}
    # Delete by ID directly — NOT by title (same-name notes are separate)
    svc._conn.execute("UPDATE notes SET deleted = 1 WHERE id = ?", (note_id,))
    svc._conn.commit()
    return {"deleted": True}


# ---------------------------------------------------------------------------
# To-Do Lists
# ---------------------------------------------------------------------------
@router.get("/todos")
async def list_todos():
    svc = _get_svc()
    return {"lists": svc.list_todo_lists()}


@router.post("/todos")
async def create_todo_list(body: TodoListCreate):
    svc = _get_svc()
    lst = svc.create_todo_list(title=body.title, items=body.items)
    return {"list": lst}


@router.delete("/todos/{list_id}")
async def delete_todo_list(list_id: int):
    svc = _get_svc()
    lst = svc.get_todo_list(list_id)
    if not lst:
        return {"error": "List not found"}
    # Delete by ID directly — NOT by title (same-name lists are separate)
    svc._conn.execute("UPDATE todo_lists SET deleted = 1 WHERE id = ?", (list_id,))
    svc._conn.execute("UPDATE todo_items SET deleted = 1 WHERE list_id = ?", (list_id,))
    svc._conn.commit()
    return {"deleted": True}


# ---------------------------------------------------------------------------
# To-Do Items
# ---------------------------------------------------------------------------
@router.post("/todos/{list_id}/items")
async def add_todo_items(list_id: int, body: TodoItemAdd):
    svc = _get_svc()
    lst = svc.get_todo_list(list_id)
    if not lst:
        return {"error": "List not found"}
    result = svc.add_todo_items(list_name=lst["title"], items=body.items)
    return {"list": result}


@router.put("/todos/items/{item_id}/done")
async def toggle_todo_done(item_id: int, body: TodoItemDone):
    svc = _get_svc()
    from app.services.notes_service import _now_ist, _iso
    conn = svc._conn
    now = _iso(_now_ist())
    conn.execute(
        "UPDATE todo_items SET done = ?, completed_at = ? WHERE id = ? AND deleted = 0",
        (1 if body.done else 0, now if body.done else None, item_id),
    )
    conn.commit()
    return {"done": body.done}


@router.delete("/todos/items/{item_id}")
async def delete_todo_item(item_id: int):
    svc = _get_svc()
    svc._conn.execute(
        "UPDATE todo_items SET deleted = 1 WHERE id = ?", (item_id,)
    )
    svc._conn.commit()
    return {"deleted": True}
