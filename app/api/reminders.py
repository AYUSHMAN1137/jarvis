"""API routes for Reminders (M8).

Endpoints:
  GET  /api/reminders          — list active reminders
  POST /api/reminders          — create a reminder
  DELETE /api/reminders/{id}   — delete a reminder
  POST /api/reminders/{id}/snooze  — snooze a reminder
  POST /api/reminders/{id}/done    — mark a reminder as done
  GET  /api/notifications/stream   — SSE stream for real-time notifications
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger("J.A.R.V.I.S")

router = APIRouter(prefix="/api", tags=["reminders"])


# ---------------------------------------------------------------------------
# Notification bus: scheduler thread pushes events, SSE stream reads them
# ---------------------------------------------------------------------------
class NotificationBus:
    """Simple pub-sub for reminder notifications → SSE clients."""

    def __init__(self):
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._event_id = 0

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=100)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def publish(self, event: Dict[str, Any]) -> None:
        with self._lock:
            self._event_id += 1
            event["event_id"] = self._event_id
            dead = []
            for q in self._subscribers:
                try:
                    q.put_nowait(event)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                try:
                    self._subscribers.remove(q)
                except ValueError:
                    pass

    @property
    def last_event_id(self) -> int:
        return self._event_id


# Global bus — used by the scheduler callback and the SSE endpoint
notification_bus = NotificationBus()


def on_reminder_fire(reminder: Dict[str, Any]) -> None:
    """Callback invoked by the ReminderScheduler when a reminder is due."""
    notification_bus.publish({
        "type": "reminder",
        "title": reminder.get("title", ""),
        "description": reminder.get("description", ""),
        "id": reminder.get("id"),
        "recurrence": reminder.get("recurrence"),
    })


# ---------------------------------------------------------------------------
# SSE notification stream
# ---------------------------------------------------------------------------
@router.get("/notifications/stream")
async def notification_stream(request: Request):
    """Server-Sent Events stream for real-time notifications (reminders, etc.).

    The frontend connects once on page load and receives push events when
    reminders fire. Supports Last-Event-ID for reconnection.
    """
    last_id_header = request.headers.get("Last-Event-ID", "0")
    try:
        last_id = int(last_id_header)
    except (ValueError, TypeError):
        last_id = 0

    q = notification_bus.subscribe()

    async def event_generator():
        try:
            # Send a heartbeat comment so the connection stays alive
            yield ": heartbeat\n\n"
            while True:
                # Check for disconnect
                if await request.is_disconnected():
                    break
                try:
                    # Non-blocking poll with short sleep
                    event = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: q.get(timeout=30)
                    )
                    event_id = event.get("event_id", 0)
                    data = json.dumps(event)
                    yield f"id: {event_id}\ndata: {data}\n\n"
                except queue.Empty:
                    # Send keepalive comment every 30 seconds
                    yield ": keepalive\n\n"
        finally:
            notification_bus.unsubscribe(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------
class ReminderCreate(BaseModel):
    title: str
    due_at: str
    recurrence: str = None
    description: str = ""
    tags: str = ""


class ReminderSnooze(BaseModel):
    minutes: int = Field(default=10, ge=1, le=1440)


def _get_svc():
    from app.services.reminder_service import get_reminder_service
    return get_reminder_service()


@router.get("/reminders")
async def list_reminders(filter: str = "all"):
    svc = _get_svc()
    return {"reminders": svc.list_active(filter_type=filter)}


@router.post("/reminders")
async def create_reminder(body: ReminderCreate):
    svc = _get_svc()
    reminder = svc.add(
        title=body.title, due_at=body.due_at,
        recurrence=body.recurrence, description=body.description,
        tags=body.tags,
    )
    return {"reminder": reminder}


@router.delete("/reminders/{reminder_id}")
async def delete_reminder(reminder_id: int):
    svc = _get_svc()
    svc.delete(reminder_id)
    return {"deleted": True}


@router.post("/reminders/{reminder_id}/snooze")
async def snooze_reminder(reminder_id: int, body: ReminderSnooze = None):
    svc = _get_svc()
    mins = body.minutes if body else 10
    result = svc.snooze(reminder_id=reminder_id, minutes=mins)
    if result:
        return {"reminder": result}
    return {"error": "Reminder not found or already snoozed."}


@router.post("/reminders/{reminder_id}/done")
async def mark_done(reminder_id: int):
    svc = _get_svc()
    svc.mark_done(reminder_id)
    return {"done": True}
