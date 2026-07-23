"""Phase 7 -- pure state-diff -> event helper.

`diff_state(prev, curr)` turns two watcher `get_state()` snapshots into a list
of `(event_type, payload)` tuples. It is intentionally PURE (no psutil, no bus,
no COM) so it can be unit-tested in any sandbox and so the watcher thread never
does anything heavy or COM-related while emitting events (the recurring
cross-thread COM crash must never be re-triggered here).

The very first diff (no previous snapshot) returns [] so we don't emit an
"app.opened" storm for every window that already existed at startup.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

# event types
EVT_APP_OPENED = "app.opened"
EVT_APP_CLOSED = "app.closed"
EVT_WINDOW_FOCUSED = "window.focused"
EVT_CLIPBOARD_CHANGED = "clipboard.changed"
EVT_SETTINGS_CHANGED = "settings.changed"

ALL_EVENTS = (
    EVT_APP_OPENED,
    EVT_APP_CLOSED,
    EVT_WINDOW_FOCUSED,
    EVT_CLIPBOARD_CHANGED,
    EVT_SETTINGS_CHANGED,
)

# Hard cap so a noisy tick (e.g. many windows appearing at once) can never
# flood the bus.
_MAX_EVENTS = 12


def diff_state(
    prev: Dict[str, Any] | None,
    curr: Dict[str, Any] | None,
) -> List[Tuple[str, Dict[str, Any]]]:
    """Return [(event_type, payload), ...] describing what changed.

    Pure + defensive: any odd input degrades to [] rather than raising.
    """
    events: List[Tuple[str, Dict[str, Any]]] = []
    try:
        if not curr:
            return []
        # First snapshot just establishes a baseline -- no events.
        if not prev:
            return []

        prev_windows = set(prev.get("windows") or [])
        curr_windows = set(curr.get("windows") or [])
        for title in sorted(curr_windows - prev_windows):
            events.append((EVT_APP_OPENED, {"title": title}))
        for title in sorted(prev_windows - curr_windows):
            events.append((EVT_APP_CLOSED, {"title": title}))

        prev_active = (prev.get("active_window") or "").strip()
        curr_active = (curr.get("active_window") or "").strip()
        if curr_active and curr_active != prev_active:
            events.append((EVT_WINDOW_FOCUSED, {"title": curr_active}))

        prev_clip = prev.get("clipboard_preview") or ""
        curr_clip = curr.get("clipboard_preview") or ""
        if curr_clip and curr_clip != prev_clip:
            # Keep the preview (RAM-only, never logged) so habits can use it,
            # but also expose length for privacy-conscious consumers.
            events.append((EVT_CLIPBOARD_CHANGED, {
                "preview": curr_clip,
                "length": len(curr_clip),
            }))

        prev_settings = prev.get("settings") or {}
        curr_settings = curr.get("settings") or {}
        if isinstance(prev_settings, dict) and isinstance(curr_settings, dict):
            for key in sorted(curr_settings.keys()):
                if key in prev_settings and prev_settings[key] != curr_settings[key]:
                    events.append((EVT_SETTINGS_CHANGED, {
                        "key": key,
                        "old": prev_settings[key],
                        "new": curr_settings[key],
                    }))
    except Exception:  # noqa: BLE001 - never let a diff crash the watcher
        return events[:_MAX_EVENTS]
    return events[:_MAX_EVENTS]
