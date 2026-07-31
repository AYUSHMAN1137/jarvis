"""
Debug Log Viewer for J.A.R.V.I.S.

There are three log files in data/debug_logs/:
  trace.log             every line from every session, continuous  <- read this
  session_<ts>_<sid>.log one file per chat session (all its turns)
  server.log            the console/stdlib log (third-party tracebacks, COM)

Usage:
    python scripts/view_debug_log.py                 # tail the trace log live
    python scripts/view_debug_log.py -s              # show the latest session file
    python scripts/view_debug_log.py -l              # list all log files
    python scripts/view_debug_log.py -n 400          # last 400 lines of trace.log
    python scripts/view_debug_log.py --uia           # only UI-automation lines
    python scripts/view_debug_log.py --tools         # only tool calls/results
    python scripts/view_debug_log.py --errors        # only errors and failures
    python scripts/view_debug_log.py --turn 7        # only turn 07
    python scripts/view_debug_log.py --server        # tail server.log instead
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "debug_logs"
TRACE = LOG_DIR / "trace.log"
SERVER = LOG_DIR / "server.log"

FILTERS = {
    "uia": ("UIA", "UIA-DIAG", "UIA-CAND", "UIA-TREE"),
    "tools": ("TOOL CALL", "TOOL OK", "TOOL FAIL", "FAST-PATH"),
    "errors": ("ERROR", "FAIL", "TIMEOUT", "Traceback", "unresolved", "unsupported"),
    "llm": ("LLM CALL", "LLM REPLY", "LLM TEXT"),
    "verify": ("VERIFY", "CACHE", "EXECUTION"),
}


def sessions() -> list[Path]:
    return sorted(LOG_DIR.glob("session_*.log"),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def list_logs() -> None:
    print(f"\n  Location: {LOG_DIR}\n")
    for path in (TRACE, SERVER):
        if path.exists():
            st = path.stat()
            when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))
            print(f"  {path.name:<40} {st.st_size:>10,} bytes  {when}")
    print()
    for i, log in enumerate(sessions()[:25]):
        st = log.stat()
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))
        print(f"  [{i}] {log.name:<38} {st.st_size:>10,} bytes  {when}")
    print()


class Keeper:
    """Line filter that keeps indented continuation lines with their parent.

    A log record is one header line plus any number of indented detail lines
    (candidate scores, control trees, tracebacks). Filtering line-by-line would
    throw the detail away, which is exactly the part worth reading.
    """

    def __init__(self, keys: list[str], turn: int | None) -> None:
        self.tokens = tuple(t for k in keys for t in FILTERS[k])
        self.turn = turn
        self.last = True

    def __call__(self, line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return self.last
        if line[:1].isspace() or stripped.startswith(("=", "-", "~", "|", ">")):
            return self.last
        if self.turn is not None and f"[T{self.turn:02d}]" not in line:
            self.last = False
            return False
        self.last = not self.tokens or any(t in line for t in self.tokens)
        return self.last


def show(path: Path, lines: int, keys: list[str], turn: int | None) -> None:
    if not path.exists():
        print(f"\n  {path.name} does not exist yet. Send a message to J.A.R.V.I.S first.\n")
        return
    print(f"\n{'=' * 70}\n  {path}\n{'=' * 70}\n")
    body = path.read_text(encoding="utf-8", errors="replace").split("\n")
    keeper = Keeper(keys, turn)
    shown = [ln for ln in body if keeper(ln)]
    for ln in shown[-lines:]:
        print(ln)


def follow(path: Path, keys: list[str], turn: int | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    print(f"\n  Following {path.name}  (Ctrl+C to stop)\n")
    keeper = Keeper(keys, turn)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        f.seek(0, 2)
        try:
            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.25)
                    continue
                if keeper(line.rstrip("\n")):
                    print(line, end="")
        except KeyboardInterrupt:
            print("\n  Stopped.\n")


def main() -> None:
    p = argparse.ArgumentParser(add_help=True)
    p.add_argument("-l", "--list", action="store_true", help="List log files.")
    p.add_argument("-s", "--session", action="store_true", help="Read the latest session file.")
    p.add_argument("--server", action="store_true", help="Read server.log.")
    p.add_argument("-n", "--lines", type=int, default=0, help="Show the last N lines instead of following.")
    p.add_argument("--turn", type=int, default=None, help="Only lines from this turn number.")
    for key in FILTERS:
        p.add_argument(f"--{key}", action="store_true", help=f"Only {key} lines.")
    args = p.parse_args()

    if not LOG_DIR.exists():
        print(f"Log directory not found: {LOG_DIR}")
        return
    if args.list:
        list_logs()
        return

    keys = [k for k in FILTERS if getattr(args, k)]
    target = TRACE
    if args.server:
        target = SERVER
    elif args.session:
        found = sessions()
        if not found:
            print("No session logs yet.")
            return
        target = found[0]

    if args.lines or args.session:
        show(target, args.lines or 500, keys, args.turn)
        return
    follow(target, keys, args.turn)


if __name__ == "__main__":
    main()
