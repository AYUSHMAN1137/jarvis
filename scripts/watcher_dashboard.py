#!/usr/bin/env python3
"""Terminal dashboard for the JARVIS system-state watcher.

Run this in a separate terminal WHILE the JARVIS server is running:
    python watcher_dashboard.py

It polls http://localhost:8000/api/watcher/state every couple of seconds and
prints a clean, live view of what the watcher daemon currently sees. Uses only
the Python standard library (no extra installs).
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

URL = os.environ.get("JARVIS_WATCHER_URL", "http://localhost:8000/api/watcher/state")
INTERVAL = 2.0


def _clear() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def _fetch() -> dict:
    with urllib.request.urlopen(URL, timeout=4) as r:
        return json.loads(r.read().decode("utf-8"))


def _ago(ts) -> str:
    if not ts:
        return "-"
    s = max(0, int(time.time() - ts))
    if s < 60:
        return f"{s}s ago"
    m, s = divmod(s, 60)
    return f"{m}m {s}s ago"


def _render(data: dict) -> None:
    _clear()
    running = data.get("running")
    badge = "RUNNING" if running else "STOPPED"
    print("=" * 62)
    print(f"  J.A.R.V.I.S   System State Watcher        [{badge}]")
    print("=" * 62)
    print(f"  Processes tracked : {data.get('process_count', 0)}")
    print(f"  Active window     : {data.get('active_window') or '-'}")
    print(f"  Refresh interval  : {data.get('interval')}s")

    launched = data.get("launched") or []
    print("\n  Apps JARVIS opened (\"close it\" registry):")
    if not launched:
        print("    (none yet - ask JARVIS to open an app)")
    else:
        for a in launched:
            pids = ",".join(str(p) for p in a.get("pids", []))
            name = a.get("name") or "?"
            print(f"    - {name:<18} pid={pids:<14} {_ago(a.get('opened_at'))}")
            if a.get("exe"):
                print(f"        exe: {a['exe']}")

    windows = data.get("windows") or []
    print(f"\n  Open windows ({len(windows)}):")
    if not windows:
        print("    (none)")
    else:
        for t in windows[:25]:
            print(f"    - {t}")
        if len(windows) > 25:
            print(f"    ... and {len(windows) - 25} more")

    print("\n" + "-" * 62)
    print(f"  Updated {time.strftime('%H:%M:%S')}  |  Ctrl+C to exit")


def main() -> None:
    while True:
        try:
            data = _fetch()
        except Exception as e:  # noqa: BLE001
            _clear()
            print("J.A.R.V.I.S  Watcher Dashboard (terminal)")
            print("=" * 50)
            print(f"\n  Cannot reach server: {e}")
            print(f"  URL: {URL}")
            print("\n  Is the JARVIS server running?  (python run.py)")
            time.sleep(INTERVAL)
            continue
        _render(data)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nbye.")
        sys.exit(0)
