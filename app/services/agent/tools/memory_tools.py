"""
Memory tools -- let the agent explicitly save / look up / forget things the user
asks it to remember, and log corrections. Registered into the shared registry
like every other tool (imported from tools/__init__.py).

Reliability: get_memory() is always fail-soft, so these tools never crash the
agent loop; the registry also converts any exception into a readable ERROR.
"""

from __future__ import annotations

import logging

from app.services.agent.tool_registry import tool
from app.services.memory_service import get_memory

logger = logging.getLogger("J.A.R.V.I.S")


@tool(
    name="remember",
    description=(
        "Save a durable fact or preference about the user to long-term memory so "
        "you recall it in future conversations. Use when the user says things "
        "like 'remember that...', 'yaad rakho...', 'note that...', or states a "
        "lasting preference (favourite app, default browser, their name, etc.). "
        "Do NOT use this for passwords, OTPs or other secrets."
    ),
    params={
        "fact": {
            "type": "string",
            "description": "The fact to remember, as a short clear sentence.",
            "required": True,
        },
        "category": {
            "type": "string",
            "description": "Type of memory.",
            "required": False,
            "enum": ["user", "preference", "project", "feedback", "general"],
        },
    },
    category="system",
)
def remember(fact: str, category: str = "general") -> str:
    return get_memory().remember(fact, category=category, source="assistant")


@tool(
    name="recall",
    description=(
        "Search the user's long-term memory for something you saved earlier "
        "(facts, preferences, past corrections, recent actions). Use when you "
        "need to remember what the user told you before answering or acting."
    ),
    params={
        "query": {
            "type": "string",
            "description": "What to look up in memory.",
            "required": True,
        },
    },
    category="system",
)
def recall(query: str) -> str:
    res = get_memory().recall(query)
    return res or "I don't have anything saved about that yet."


@tool(
    name="forget",
    description=(
        "Delete things from the user's long-term memory that match a query. Use "
        "when the user says 'forget that', 'bhul jao', or asks to remove a saved "
        "fact."
    ),
    params={
        "query": {
            "type": "string",
            "description": "What to forget (matched against saved facts).",
            "required": True,
        },
    },
    category="system",
)
def forget(query: str) -> str:
    n = get_memory().forget(query)
    return f"Removed {n} saved item(s)." if n else "I couldn't find a matching memory to remove."


@tool(
    name="note_correction",
    description=(
        "Record a correction so you never repeat the same mistake. Use when the "
        "user corrects you, e.g. 'no, not like that', 'nahi aise nahi', or 'I "
        "said Brave not Chrome'."
    ),
    params={
        "wrong": {
            "type": "string",
            "description": "What was wrong / the mistake you made.",
            "required": True,
        },
        "right": {
            "type": "string",
            "description": "What the user actually wanted.",
            "required": True,
        },
    },
    category="system",
)
def note_correction(wrong: str, right: str) -> str:
    return get_memory().record_correction(wrong, right) or "Noted."
