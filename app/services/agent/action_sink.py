"""
Thread-local sink for collecting *frontend actions* produced during a single
agent run.

Web/content tools run on the server but their real effect (open a browser tab,
show a generated image) happens in the user's browser. Instead of returning
complex objects through the LLM, these tools push lightweight actions here.
The agent loop opens a fresh sink at the start of each run and reads it at the
end, then hands the collected actions to the chat layer as an `_actions` event.

Thread-local so concurrent requests never mix their actions.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any, Dict, List, Optional

_local = threading.local()


def _bucket() -> Dict[str, List[Any]]:
    b = getattr(_local, "bucket", None)
    if b is None:
        b = _new_bucket()
        _local.bucket = b
    return b


def _new_bucket() -> Dict[str, List[Any]]:
    return {
        "wopens": [],
        "plays": [],
        "images": [],
        "contents": [],
        "googlesearches": [],
        "youtubesearches": [],
        "cam": None,
        "_meta": {},
    }


def reset() -> None:
    """Start a fresh collection for the current thread/run."""
    _local.bucket = _new_bucket()


def add_open(url: str) -> None:
    _bucket()["wopens"].append(url)


def add_play(url: str) -> None:
    _bucket()["plays"].append(url)


def add_google(url: str) -> None:
    _bucket()["googlesearches"].append(url)


def add_youtube(url: str) -> None:
    _bucket()["youtubesearches"].append(url)


def add_image(url: str) -> None:
    _bucket()["images"].append(url)


def add_content(text: str) -> None:
    _bucket()["contents"].append(text)


def set_cam(payload: Optional[dict]) -> None:
    _bucket()["cam"] = payload


def attach_dispatch(execution_id: str, action_id: str) -> None:
    """Attach correlation data to browser-bound actions for acknowledgement."""
    b = _bucket()
    if not has_actions():
        return
    b["_meta"] = {
        "dispatch_id": uuid.uuid4().hex,
        "execution_id": str(execution_id or ""),
        "action_id": str(action_id or ""),
    }


def collect() -> Dict[str, Any]:
    """Return the current actions and whether any exist."""
    return dict(_bucket())


def has_actions() -> bool:
    b = _bucket()
    return any([
        b["wopens"], b["plays"], b["images"], b["contents"],
        b["googlesearches"], b["youtubesearches"], b["cam"],
    ])
