"""
Web tools — these produce *frontend actions*.

Unlike desktop tools (which run on the server), these tools return a small
marker the agent loop collects into a `frontend_actions` list. The chat layer
then emits those as an `_actions` event so the user's browser opens the URL,
plays the video, etc. This preserves the original Jarvis behaviour where the
browser (client side) performs the open/play/search.

Each function returns a string starting with "ACTION:" plus a JSON-ish payload
that the agent loop recognises. To keep things simple and robust we instead
register a side-effect via a thread-local collector set by the agent loop.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional
from urllib.parse import quote

from app.services.agent.tool_registry import tool
from app.services.agent import action_sink

logger = logging.getLogger("J.A.R.V.I.S")

_SITE_MAP = {
    "facebook": "https://www.facebook.com", "instagram": "https://www.instagram.com",
    "youtube": "https://www.youtube.com", "google": "https://www.google.com",
    "netflix": "https://www.netflix.com", "twitter": "https://twitter.com",
    "x": "https://x.com", "gmail": "https://mail.google.com",
    "whatsapp": "https://web.whatsapp.com", "linkedin": "https://www.linkedin.com",
    "reddit": "https://www.reddit.com", "discord": "https://discord.com/app",
    "spotify": "https://open.spotify.com", "amazon": "https://www.amazon.com",
    "github": "https://github.com", "wikipedia": "https://www.wikipedia.org",
    "maps": "https://www.google.com/maps", "drive": "https://drive.google.com",
}


def _normalize_url(target: str) -> str:
    t = (target or "").strip()
    low = t.lower()
    if low in _SITE_MAP:
        return _SITE_MAP[low]
    if t.startswith("http://") or t.startswith("https://"):
        return t
    if "." in t and " " not in t:
        return "https://" + t
    # Local addresses such as "localhost" or "localhost:8000" are not public .com sites.
    if low == "localhost" or low.startswith("localhost:"):
        return "http://" + t
    return f"https://www.{low.replace(' ', '')}.com"


# Verb sets for the deterministic fast-path (English + common Hindi).
_OPEN_VERBS = r"open|launch|start|go to|goto|visit|kholo|khol do|khol"
_PLAY_VERBS = r"play|chalao|chala do|bajao|baja do"
_SEARCH_VERBS = r"google|search for|search"


def try_direct_web_command(message: str) -> Optional[Dict[str, Any]]:
    """Deterministic fast-path for explicit, unambiguous web commands.

    Voice commands like "open youtube" / "play X" / "search Y" are mechanical
    actions. LLM tool-calling for them is unreliable (Groq sometimes emits a
    malformed tool call, Gemini sometimes just replies "Done." without acting),
    so for these clear cases we map straight to the right web tool -- instant
    and 100%% reliable. Anything ambiguous (e.g. "open notepad", a desktop app)
    returns None and is handled by the normal LLM agent. Only reached for
    task-routed messages, so everyday chit-chat never lands here.
    """
    if not message:
        return None
    low = message.strip().lower().strip(" .!?\u0964")
    if not low:
        return None

    # ---- play on youtube ----
    m = re.match(rf"^(?:{_PLAY_VERBS})\s+(.+)$", low) or re.match(rf"^(.+?)\s+(?:{_PLAY_VERBS})$", low)
    if m:
        q = m.group(1).strip()
        q = re.sub(r"\s+(?:on|pe|par)\s+youtube$", "", q).strip()
        # Also strip a LEADING "youtube pe/par/me" + a filler word so
        # "youtube pe koi gaana chalao" searches the song, not the literal
        # "youtube pe koi gaana".
        q = re.sub(r"^youtube\s+(?:pe|par|pr|me|mein)\s+", "", q).strip()
        q = re.sub(r"^(?:koi|kuch|ek|song|gana|gaana|video)\s+", "", q).strip()
        if q:
            return {"tool": "play_on_youtube", "args": {"query": q},
                    "say": f"Playing {q} on YouTube, Sir."}

    # ---- google search ----
    m = re.match(rf"^(?:{_SEARCH_VERBS})\s+(.+)$", low)
    if m:
        q = re.sub(r"\s+(?:on|pe|par)\s+google$", "", m.group(1).strip()).strip()
        if q:
            return {"tool": "search_google", "args": {"query": q},
                    "say": f"Searching Google for {q}, Sir."}

    # ---- open website (only when clearly a site; desktop apps go to the agent) ----
    m = re.match(rf"^(?:{_OPEN_VERBS})\s+(.+)$", low) or re.match(r"^(.+?)\s+(?:kholo|khol do|open karo)$", low)
    if m:
        target = m.group(1).strip()
        target = re.sub(r"^(?:the\s+|website\s+|site\s+|app\s+)", "", target).strip()
        target = re.sub(r"\s+(?:website|site)$", "", target).strip()
        is_site = (
            target in _SITE_MAP
            or target.startswith("http://") or target.startswith("https://")
            or ("." in target and " " not in target)
        )
        if is_site:
            return {"tool": "open_website", "args": {"target": target},
                    "say": f"Opening {target}, Sir."}

    return None


@tool(
    name="open_website",
    description=(
        "Open a website / URL in the user's browser. Use for web addresses and "
        "known sites (youtube, facebook, gmail, github, etc.). NOT for desktop apps."
    ),
    params={
        "target": {
            "type": "string",
            "description": "A URL or a known site name, e.g. 'youtube.com' or 'github'.",
        }
    },
    category="web",
)
def open_website(target: str) -> str:
    url = _normalize_url(target)
    action_sink.add_open(url)
    logger.info("[WEB] open_website -> %s", url)
    return f"Opening {url} in the browser."


@tool(
    name="play_on_youtube",
    description="Search and play a song or video on YouTube in the browser.",
    params={"query": {"type": "string", "description": "Song or video to play."}},
    category="web",
)
def play_on_youtube(query: str) -> str:
    q = (query or "").strip()
    if not q:
        action_sink.add_open("https://www.youtube.com")
        return "Opening YouTube."
    url = f"https://www.youtube.com/results?search_query={quote(q, safe='')}"
    action_sink.add_play(url)
    logger.info("[WEB] play_on_youtube -> %s", q)
    return f"Playing '{q}' on YouTube."


@tool(
    name="search_google",
    description="Run a Google search and open the results in the browser.",
    params={"query": {"type": "string", "description": "What to search on Google."}},
    category="web",
)
def search_google(query: str) -> str:
    q = (query or "").strip()
    if not q:
        return "ERROR: empty search query."
    url = f"https://www.google.com/search?q={quote(q, safe='')}"
    action_sink.add_google(url)
    logger.info("[WEB] search_google -> %s", q)
    return f"Searching Google for '{q}'."


@tool(
    name="search_youtube",
    description="Open YouTube search results for a query (without auto-playing).",
    params={"query": {"type": "string", "description": "What to search on YouTube."}},
    category="web",
)
def search_youtube(query: str) -> str:
    q = (query or "").strip()
    if not q:
        action_sink.add_open("https://www.youtube.com")
        return "Opening YouTube."
    url = f"https://www.youtube.com/results?search_query={quote(q, safe='')}"
    action_sink.add_youtube(url)
    logger.info("[WEB] search_youtube -> %s", q)
    return f"Searching YouTube for '{q}'."
