"""
Thread-local sink for collecting *frontend actions* produced during a single
tool call.

Web/content tools run on the server but their real effect (open a browser tab,
show a generated image) happens in the user's browser. Instead of returning
complex objects through the LLM, these tools push lightweight actions here.

Lifecycle (M13 §3.2): the sink is **drained per action**, not per run.
`ExecutionCoordinator.execute_action` calls `attach_dispatch()` then
`collect(drain=True)` immediately after the tool returns, so:

  * each frontend action carries its own `_meta` (and therefore its own
    `dispatch_id`), which is what makes per-action acknowledgement possible;
  * a multi-step run can never re-emit step 1's URL when step 2 finishes.

Before this, `collect()` never cleared and `_meta` was overwritten on every
call, so step 2 re-opened step 1's tab and only the last action of a run could
ever be acknowledged.

Thread-local so concurrent requests never mix their actions.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any, Dict, List, Optional

_local = threading.local()

# Keys that carry real browser work. `_meta` is correlation data, not an action,
# so it must never make a bucket look non-empty.
_ACTION_KEYS = (
    "wopens", "plays", "images", "contents",
    "googlesearches", "youtubesearches", "cam", "panels",
)


def _bucket() -> Dict[str, Any]:
    b = getattr(_local, "bucket", None)
    if b is None:
        b = _new_bucket()
        _local.bucket = b
    return b


def _new_bucket() -> Dict[str, Any]:
    return {
        "wopens": [],
        "plays": [],
        "images": [],
        "contents": [],
        "googlesearches": [],
        "youtubesearches": [],
        "cam": None,
        "panels": {},
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


def set_panel(panel_name: str, payload: dict) -> None:
    """Push a panel action (open/refresh/close) to the frontend.

    panel_name: 'reminders' or 'notes'
    payload: {'action': 'open'|'refresh'|'close', 'tab': 'notes'|'todo', ...}
    """
    _bucket()["panels"][panel_name] = payload


def attach_dispatch(execution_id: str, action_id: str) -> Optional[str]:
    """Stamp correlation data on the pending browser-bound actions.

    Returns the freshly minted ``dispatch_id`` so the caller can register it
    with the Phase 4 coordinator *before* the payload reaches the browser.
    Returns None when this action produced nothing for the browser to do --
    there is then nothing to acknowledge.
    """
    b = _bucket()
    if not has_actions():
        return None
    dispatch_id = uuid.uuid4().hex
    b["_meta"] = {
        "dispatch_id": dispatch_id,
        "execution_id": str(execution_id or ""),
        "action_id": str(action_id or ""),
    }
    return dispatch_id


def collect(drain: bool = False) -> Dict[str, Any]:
    """Return the current actions.

    ``drain=True`` hands the bucket over and immediately starts a fresh one, so
    the same payload can never be emitted twice. Callers that only peek (tests,
    diagnostics) leave it False.
    """
    b = _bucket()
    if drain:
        _local.bucket = _new_bucket()
        return b
    return dict(b)


def bucket_has_actions(bucket: Optional[Dict[str, Any]]) -> bool:
    """True when an already-collected bucket carries real browser work."""
    if not bucket:
        return False
    return any(bucket.get(key) for key in _ACTION_KEYS)


def has_actions() -> bool:
    return bucket_has_actions(_bucket())
