"""System State Watcher package.

A lightweight background daemon that continuously tracks what is running on the
machine (processes, windows, and the apps JARVIS itself launched) so the rest of
JARVIS can act on the REAL system state instead of guessing.
"""

from app.services.watcher.state_service import SystemStateWatcher, get_watcher

__all__ = ["SystemStateWatcher", "get_watcher"]
