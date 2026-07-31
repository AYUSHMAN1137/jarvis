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
from urllib.parse import quote

from app.services.agent.tool_registry import tool
from app.services.agent import action_sink
from app.services.debug_logger import dbg

logger = logging.getLogger("J.A.R.V.I.S")

def _normalize_url(target: str) -> str:
    """Turn whatever the agent passed into a URL the browser can open.

    M13 §4.3 removed `_SITE_MAP`, the hand-written name -> URL table. It was a
    guess about language ("x" means twitter.com, "drive" means Google Drive) and
    every miss needed another line. The agent already knows what URL a site has;
    all this has to do is normalise a string into a valid address.

    What stays is purely mechanical: a scheme, a dotted hostname, a local
    address, or a bare name that gets the conventional www/.com shape.
    """
    raw = (target or "").strip().strip("<>\"'")
    if not raw:
        return "https://www.google.com"
    low = raw.lower()
    if low.startswith(("http://", "https://")):
        return raw
    # Local addresses ("localhost", "localhost:8000", "127.0.0.1:8000") are not
    # public .com sites and are not https either.
    host = low.split("/", 1)[0]
    if host == "localhost" or host.startswith("localhost:") or host.startswith("127.0.0.1"):
        return "http://" + raw
    if "." in host and " " not in host:
        return "https://" + raw
    return "https://www." + re.sub(r"\s+", "", low) + ".com"


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
    verification={"family": "frontend"},
)
def open_website(target: str) -> str:
    url = _normalize_url(target)
    action_sink.add_open(url)
    logger.info("[WEB] open_website -> %s", url)
    return f"Opening {url} in the browser."


@tool(
    name="play_on_youtube",
    description=(
        "Search YouTube and automatically play the first video result. "
        "Fetches search results, extracts the first video, and opens it "
        "directly with autoplay. Fully self-contained — no follow-up calls needed."
    ),
    params={"query": {"type": "string", "description": "Song or video to search and play."}},
    category="web",
    verification={"family": "frontend"},
)
def play_on_youtube(query: str) -> str:
    import re as _re
    import webbrowser as _wb

    import requests as _requests

    q = (query or "").strip()
    if not q:
        action_sink.add_open("https://www.youtube.com")
        return "Opened YouTube homepage."

    logger.info("[WEB] play_on_youtube -> %s", q)

    # Step 1: Fetch YouTube search page HTML server-side (fast, ~1-2s)
    search_url = f"https://www.youtube.com/results?search_query={quote(q, safe='')}"
    video_id = None
    video_title = None
    try:
        resp = _requests.get(
            search_url,
            params={"hl": "en", "gl": "IN"},
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
            },
            timeout=10,
        )
        html = resp.text

        # Extract first videoId from the initial data JSON embedded in the page
        # YouTube embeds ytInitialData with videoRenderer objects
        ids = _re.findall(r'"videoId"\s*:\s*"([a-zA-Z0-9_-]{11})"', html)
        if ids:
            # De-duplicate while preserving order; skip Shorts (usually first few)
            seen = set()
            unique_ids = []
            for vid in ids:
                if vid not in seen:
                    seen.add(vid)
                    unique_ids.append(vid)
            video_id = unique_ids[0]

        # Try to grab the title of the first video
        if video_id:
            title_match = _re.search(
                r'"videoId"\s*:\s*"' + _re.escape(video_id) + r'".*?"title"\s*:\s*\{\s*"runs"\s*:\s*\[\s*\{\s*"text"\s*:\s*"([^"]+)"',
                html,
            )
            if title_match:
                video_title = title_match.group(1)
    except Exception as e:  # noqa: BLE001
        logger.warning("[WEB] play_on_youtube fetch failed: %s", e)

    # Step 2: Open the video directly (or search page as fallback)
    if video_id:
        video_url = f"https://www.youtube.com/watch?v={video_id}&autoplay=1"
        logger.info("[WEB] play_on_youtube -> video_id=%s title=%s", video_id, video_title or "?")
        try:
            _wb.open(video_url)
        except Exception:  # noqa: BLE001
            action_sink.add_play(video_url)
        title_str = f' "{video_title}"' if video_title else ""
        # Honest wording: we opened the video page. We did NOT confirm playback,
        # and claiming we did is how "Now playing..." got reported for videos that
        # never started. If confirmation matters, the agent can check the browser
        # window with ui_list_controls / ui_do.
        return (f"Opened{title_str} on YouTube in the browser. It should start "
                "playing on its own; tell me if it does not.")
    else:
        # Fallback: just open the search page
        try:
            _wb.open(search_url)
        except Exception:  # noqa: BLE001
            action_sink.add_play(search_url)
        return (
            f"Could not extract a video for '{q}'. "
            f"YouTube search page is open in the browser — user can pick a video."
        )


@tool(
    name="search_google",
    description="Run a Google search and open the results in the browser.",
    params={"query": {"type": "string", "description": "What to search on Google."}},
    category="web",
    verification={"family": "frontend"},
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
    verification={"family": "frontend"},
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
