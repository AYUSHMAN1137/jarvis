"""Notes & To-Do service.

Two complementary systems in one module:

* **Notes** — free-form markdown text blobs (title + body).
* **To-Do lists** — named lists of checkable items.

Database: ``data/notes.db`` (auto-created, separate from reminders).

Design decisions (from AI review):
  * Body stored as ``markdown_body`` for future rich-text support.
  * No priority field on todo items — the list name IS the category.
  * Soft-delete via status field for potential undo.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.db import open_db

logger = logging.getLogger("J.A.R.V.I.S")

IST = timezone(timedelta(hours=5, minutes=30))

_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_DB_PATH = _BASE_DIR / "data" / "notes.db"


def _now_ist() -> datetime:
    return datetime.now(tz=IST)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT    NOT NULL,
    markdown_body TEXT    DEFAULT '',
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL,
    pinned        INTEGER DEFAULT 0,
    color         TEXT    DEFAULT 'default',
    tags          TEXT    DEFAULT '',
    deleted       INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS todo_lists (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT    NOT NULL,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL,
    pinned        INTEGER DEFAULT 0,
    deleted       INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS todo_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    list_id       INTEGER NOT NULL REFERENCES todo_lists(id) ON DELETE CASCADE,
    text          TEXT    NOT NULL,
    done          INTEGER DEFAULT 0,
    due_date      TEXT    DEFAULT NULL,
    position      INTEGER DEFAULT 0,
    created_at    TEXT    NOT NULL,
    completed_at  TEXT    DEFAULT NULL,
    deleted       INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_notes_deleted ON notes(deleted);
CREATE INDEX IF NOT EXISTS idx_todo_lists_deleted ON todo_lists(deleted);
CREATE INDEX IF NOT EXISTS idx_todo_items_list ON todo_items(list_id, deleted);
"""


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return dict(row)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_instance: Optional["NotesService"] = None
_instance_lock = threading.Lock()


def get_notes_service() -> "NotesService":
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = NotesService()
    return _instance


# ---------------------------------------------------------------------------
# NotesService
# ---------------------------------------------------------------------------
class NotesService:
    """Manages notes and to-do lists."""

    def __init__(self):
        self._conn = open_db(_DB_PATH, label="notes", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

        note_count = self._conn.execute("SELECT COUNT(*) FROM notes WHERE deleted = 0").fetchone()[0]
        todo_count = self._conn.execute("SELECT COUNT(*) FROM todo_lists WHERE deleted = 0").fetchone()[0]
        logger.info("[NOTES] Service initialized: %d notes, %d to-do lists.", note_count, todo_count)

    # ===== NOTES =====

    def create_note(self, title: str, body: str = "") -> Dict[str, Any]:
        now = _iso(_now_ist())
        cur = self._conn.execute(
            "INSERT INTO notes (title, markdown_body, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (title, body, now, now),
        )
        self._conn.commit()
        logger.info("[NOTES] Created note #%d: '%s'", cur.lastrowid, title)
        return self.get_note(cur.lastrowid)

    def get_note(self, note_id: int) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM notes WHERE id = ? AND deleted = 0", (note_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None

    def list_notes(self, query: str = None) -> List[Dict[str, Any]]:
        if query:
            q = f"%{query}%"
            rows = self._conn.execute(
                """SELECT * FROM notes WHERE deleted = 0
                   AND (title LIKE ? OR markdown_body LIKE ?)
                   ORDER BY pinned DESC, updated_at DESC""",
                (q, q),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM notes WHERE deleted = 0 ORDER BY pinned DESC, updated_at DESC"
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def edit_note(self, query: str, body: str = None, append: str = None,
                  new_title: str = None) -> Optional[Dict[str, Any]]:
        """Find note by title search and update it."""
        note = self._find_note(query)
        if not note:
            return None

        now = _iso(_now_ist())
        updates = []
        params = []

        if body is not None:
            updates.append("markdown_body = ?")
            params.append(body)
        elif append is not None:
            current = note["markdown_body"] or ""
            new_body = (current + "\n" + append).strip() if current else append
            updates.append("markdown_body = ?")
            params.append(new_body)

        if new_title is not None:
            updates.append("title = ?")
            params.append(new_title)

        if not updates:
            return note

        updates.append("updated_at = ?")
        params.append(now)
        params.append(note["id"])

        self._conn.execute(
            f"UPDATE notes SET {', '.join(updates)} WHERE id = ?", params
        )
        self._conn.commit()
        logger.info("[NOTES] Updated note #%d: '%s'", note["id"], note["title"])
        return self.get_note(note["id"])

    def delete_note(self, query: str) -> int:
        """Soft-delete notes matching a title search."""
        notes = self._find_notes(query)
        for n in notes:
            self._conn.execute(
                "UPDATE notes SET deleted = 1 WHERE id = ?", (n["id"],)
            )
        self._conn.commit()
        if notes:
            logger.info("[NOTES] Deleted %d note(s) matching '%s'", len(notes), query)
        return len(notes)

    def pin_note(self, query: str, pinned: bool = True) -> Optional[Dict[str, Any]]:
        note = self._find_note(query)
        if not note:
            return None
        self._conn.execute(
            "UPDATE notes SET pinned = ? WHERE id = ?", (1 if pinned else 0, note["id"])
        )
        self._conn.commit()
        return self.get_note(note["id"])

    def _find_note(self, query: str) -> Optional[Dict[str, Any]]:
        """Find a single note by title search."""
        notes = self._find_notes(query)
        return notes[0] if notes else None

    def _find_notes(self, query: str) -> List[Dict[str, Any]]:
        q_lower = query.lower().strip()
        rows = self._conn.execute(
            "SELECT * FROM notes WHERE deleted = 0 ORDER BY updated_at DESC"
        ).fetchall()
        results = []
        for row in rows:
            if q_lower in row["title"].lower():
                results.append(_row_to_dict(row))
        return results

    # ===== TO-DO LISTS =====

    def create_todo_list(self, title: str, items: List[str] = None) -> Dict[str, Any]:
        """Create a new to-do list, optionally with initial items."""
        now = _iso(_now_ist())
        cur = self._conn.execute(
            "INSERT INTO todo_lists (title, created_at, updated_at) VALUES (?, ?, ?)",
            (title, now, now),
        )
        list_id = cur.lastrowid

        if items:
            for i, text in enumerate(items):
                self._conn.execute(
                    "INSERT INTO todo_items (list_id, text, position, created_at) VALUES (?, ?, ?, ?)",
                    (list_id, text.strip(), i, now),
                )
        self._conn.commit()
        logger.info("[NOTES] Created to-do list #%d: '%s' with %d items", list_id, title, len(items or []))
        return self.get_todo_list(list_id)

    def get_todo_list(self, list_id: int) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM todo_lists WHERE id = ? AND deleted = 0", (list_id,)
        ).fetchone()
        if not row:
            return None
        result = _row_to_dict(row)
        items = self._conn.execute(
            "SELECT * FROM todo_items WHERE list_id = ? AND deleted = 0 ORDER BY position ASC, id ASC",
            (list_id,),
        ).fetchall()
        result["items"] = [_row_to_dict(item) for item in items]
        return result

    def list_todo_lists(self) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM todo_lists WHERE deleted = 0 ORDER BY pinned DESC, updated_at DESC"
        ).fetchall()
        results = []
        for row in rows:
            d = _row_to_dict(row)
            items = self._conn.execute(
                "SELECT * FROM todo_items WHERE list_id = ? AND deleted = 0 ORDER BY position ASC, id ASC",
                (row["id"],),
            ).fetchall()
            d["items"] = [_row_to_dict(item) for item in items]
            results.append(d)
        return results

    def find_todo_list(self, name: str) -> Optional[Dict[str, Any]]:
        """Find a to-do list by title search."""
        name_lower = name.lower().strip()
        rows = self._conn.execute(
            "SELECT * FROM todo_lists WHERE deleted = 0 ORDER BY updated_at DESC"
        ).fetchall()
        for row in rows:
            if name_lower in row["title"].lower():
                return self.get_todo_list(row["id"])
        return None

    def delete_todo_list(self, name: str) -> int:
        """Soft-delete a to-do list and its items."""
        name_lower = name.lower().strip()
        rows = self._conn.execute(
            "SELECT * FROM todo_lists WHERE deleted = 0"
        ).fetchall()
        deleted = 0
        for row in rows:
            if name_lower in row["title"].lower():
                self._conn.execute("UPDATE todo_lists SET deleted = 1 WHERE id = ?", (row["id"],))
                self._conn.execute("UPDATE todo_items SET deleted = 1 WHERE list_id = ?", (row["id"],))
                deleted += 1
        self._conn.commit()
        if deleted:
            logger.info("[NOTES] Deleted %d to-do list(s) matching '%s'", deleted, name)
        return deleted

    # ===== TO-DO ITEMS =====

    def add_todo_items(self, list_name: str, items: List[str]) -> Optional[Dict[str, Any]]:
        """Add items to an existing to-do list. Creates the list if it doesn't exist."""
        lst = self.find_todo_list(list_name)
        if not lst:
            return self.create_todo_list(list_name, items)

        now = _iso(_now_ist())
        max_pos = self._conn.execute(
            "SELECT COALESCE(MAX(position), -1) FROM todo_items WHERE list_id = ? AND deleted = 0",
            (lst["id"],),
        ).fetchone()[0]

        for i, text in enumerate(items):
            self._conn.execute(
                "INSERT INTO todo_items (list_id, text, position, created_at) VALUES (?, ?, ?, ?)",
                (lst["id"], text.strip(), max_pos + 1 + i, now),
            )
        self._conn.execute(
            "UPDATE todo_lists SET updated_at = ? WHERE id = ?", (now, lst["id"])
        )
        self._conn.commit()
        logger.info("[NOTES] Added %d item(s) to '%s'", len(items), lst["title"])
        return self.get_todo_list(lst["id"])

    def mark_todo_done(self, list_name: str, item_query: str) -> Optional[Dict[str, Any]]:
        return self._set_done(list_name, item_query, done=True)

    def mark_todo_undone(self, list_name: str, item_query: str) -> Optional[Dict[str, Any]]:
        return self._set_done(list_name, item_query, done=False)

    def _set_done(self, list_name: str, item_query: str, done: bool) -> Optional[Dict[str, Any]]:
        lst = self.find_todo_list(list_name)
        if not lst:
            return None

        q_lower = item_query.lower().strip()
        now = _iso(_now_ist())
        matched = 0
        for item in lst["items"]:
            if q_lower in item["text"].lower():
                self._conn.execute(
                    "UPDATE todo_items SET done = ?, completed_at = ? WHERE id = ?",
                    (1 if done else 0, now if done else None, item["id"]),
                )
                matched += 1
        if matched:
            self._conn.execute(
                "UPDATE todo_lists SET updated_at = ? WHERE id = ?", (now, lst["id"])
            )
            self._conn.commit()
        return self.get_todo_list(lst["id"])

    def remove_todo_items(self, list_name: str, item_query: str) -> Optional[Dict[str, Any]]:
        """Soft-delete items from a to-do list matching a search."""
        lst = self.find_todo_list(list_name)
        if not lst:
            return None

        q_lower = item_query.lower().strip()
        now = _iso(_now_ist())
        removed = 0
        for item in lst["items"]:
            if q_lower in item["text"].lower():
                self._conn.execute(
                    "UPDATE todo_items SET deleted = 1 WHERE id = ?", (item["id"],)
                )
                removed += 1
        if removed:
            self._conn.execute(
                "UPDATE todo_lists SET updated_at = ? WHERE id = ?", (now, lst["id"])
            )
            self._conn.commit()
        return self.get_todo_list(lst["id"])

    # ===== STATS =====

    def get_stats(self) -> Dict[str, Any]:
        note_count = self._conn.execute("SELECT COUNT(*) FROM notes WHERE deleted = 0").fetchone()[0]
        todo_count = self._conn.execute("SELECT COUNT(*) FROM todo_lists WHERE deleted = 0").fetchone()[0]
        item_count = self._conn.execute("SELECT COUNT(*) FROM todo_items WHERE deleted = 0").fetchone()[0]
        done_count = self._conn.execute("SELECT COUNT(*) FROM todo_items WHERE deleted = 0 AND done = 1").fetchone()[0]
        return {
            "notes": note_count,
            "todo_lists": todo_count,
            "total_items": item_count,
            "done_items": done_count,
            "pending_items": item_count - done_count,
        }
