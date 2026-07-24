"""
Quick Debug Log Viewer for J.A.R.V.I.S
Usage:
    python scripts/view_debug_log.py          # show latest log
    python scripts/view_debug_log.py -n 5     # show last 5 log files
    python scripts/view_debug_log.py -f       # follow (tail) the latest log in real-time
    python scripts/view_debug_log.py -l       # list all log files
"""

import sys
import time
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "debug_logs"


def list_logs():
    logs = sorted(LOG_DIR.glob("session_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not logs:
        print("No debug logs found yet. Start a chat session first.")
        return []
    print(f"\n{'='*60}")
    print(f"  DEBUG LOGS ({len(logs)} files)")
    print(f"  Location: {LOG_DIR}")
    print(f"{'='*60}\n")
    for i, log in enumerate(logs[:20]):
        size = log.stat().st_size
        mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(log.stat().st_mtime))
        print(f"  [{i}] {log.name}  ({size:,} bytes, {mtime})")
    print()
    return logs


def show_log(path: Path, lines: int = 200):
    print(f"\n{'='*60}")
    print(f"  {path.name}")
    print(f"{'='*60}\n")
    content = path.read_text(encoding="utf-8")
    all_lines = content.split("\n")
    for line in all_lines[-lines:]:
        print(line)


def follow_log(path: Path):
    print(f"\n  Following: {path.name}  (Ctrl+C to stop)\n")
    with open(path, "r", encoding="utf-8") as f:
        # Go to end
        f.seek(0, 2)
        try:
            while True:
                line = f.readline()
                if line:
                    print(line, end="")
                else:
                    time.sleep(0.3)
        except KeyboardInterrupt:
            print("\n  Stopped.")


def main():
    if not LOG_DIR.exists():
        print(f"Log directory not found: {LOG_DIR}")
        print("Start the JARVIS server and send a message first.")
        return

    args = sys.argv[1:]

    if "-l" in args or "--list" in args:
        list_logs()
        return

    logs = sorted(LOG_DIR.glob("session_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not logs:
        print("No debug logs found yet. Start a chat session first.")
        return

    if "-n" in args:
        idx = args.index("-n")
        count = int(args[idx + 1]) if idx + 1 < len(args) else 5
        list_logs()
        return

    if "-f" in args or "--follow" in args:
        follow_log(logs[0])
        return

    # Default: show latest log
    show_log(logs[0])


if __name__ == "__main__":
    main()
