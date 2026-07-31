"""One-off helper: stop the running server the way a user's Ctrl+C would.

Run:  .venv\\Scripts\\python.exe scripts\\_m13_graceful_stop.py

Why this exists: killing uvicorn hard skips the lifespan shutdown, so
`checkpoint_and_close_all()` never runs and every database is left with a `-wal`
sidecar. `scripts/verdict_report.py` then reports WAL bloat that looks like a bug
but is really just "the server was killed, not shut down".

Sends CTRL_BREAK to the process group that owns port 8000, then waits and reports
whether the WAL sidecars were checkpointed away.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
DBS = ["memory.db", "skills.db", "command_cache.db", "user_model.db",
       "proactive.db", "reminders.db", "notes.db", "file_index.db"]


def listening_pid(port=8000):
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                             capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "TCP" and parts[3].upper() == "LISTENING":
            if parts[1].endswith(f":{port}"):
                try:
                    return int(parts[4])
                except ValueError:
                    continue
    return None


def sidecars():
    found = []
    for name in DBS:
        for suffix in ("-wal", "-shm"):
            path = DATA / (name + suffix)
            if path.exists():
                try:
                    size = path.stat().st_size
                except OSError:
                    size = 0
                found.append(f"{path.name} ({size / 1024:.1f} KB)")
    return found


def main():
    before = sidecars()
    print(f"sidecars before: {len(before)}")
    for item in before:
        print(f"  {item}")

    pid = listening_pid()
    if pid is None:
        print("\nNothing listening on port 8000.")
    else:
        print(f"\nsending CTRL_BREAK to pid {pid} ...")
        try:
            os.kill(pid, signal.CTRL_BREAK_EVENT)
        except Exception as exc:
            print(f"  CTRL_BREAK failed ({exc}); falling back to taskkill")
            subprocess.run(["taskkill", "/PID", str(pid)], capture_output=True)
        for _ in range(30):
            time.sleep(1)
            if listening_pid() is None:
                print("  port released")
                break
        else:
            print("  still listening after 30s -- forcing")
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
            time.sleep(2)

    time.sleep(2)
    after = sidecars()
    print(f"\nsidecars after: {len(after)}")
    for item in after:
        print(f"  {item}")
    if after:
        print("\nStill present. Either the shutdown hook did not run, or another "
              "process (a test run, verdict_report) has the DB open.")
    else:
        print("\nClean: every WAL was checkpointed and closed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
