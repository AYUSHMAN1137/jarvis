"""
Lightweight dependency holder for tools that need shared services.

Some tools (image generation, content writing, gmail/calendar/drive) need
access to long-lived services created at startup (GroqService, GmailService,
etc.). Rather than passing these through every layer, the app wires them here
once during startup, and tools read them on demand.
"""

from __future__ import annotations

from typing import Any, Optional


class _Deps:
    groq_service: Optional[Any] = None
    gmail_service: Optional[Any] = None
    calendar_service: Optional[Any] = None
    drive_service: Optional[Any] = None


deps = _Deps()


def configure(
    groq_service: Any = None,
    gmail_service: Any = None,
    calendar_service: Any = None,
    drive_service: Any = None,
) -> None:
    if groq_service is not None:
        deps.groq_service = groq_service
    if gmail_service is not None:
        deps.gmail_service = gmail_service
    if calendar_service is not None:
        deps.calendar_service = calendar_service
    if drive_service is not None:
        deps.drive_service = drive_service
