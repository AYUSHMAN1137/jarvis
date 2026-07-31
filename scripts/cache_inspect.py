"""Inspect / audit / clean the verified-command cache.

    python scripts/cache_inspect.py           # list active entries
    python scripts/cache_inspect.py --audit   # show entries that cannot satisfy their command
    python scripts/cache_inspect.py --purge   # delete those entries
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.agent.cache.command_cache import CommandCache  # noqa: E402


def main() -> None:
    cache = CommandCache()
    if not cache.enabled:
        print("cache is disabled or unreadable")
        return

    if "--purge" in sys.argv:
        purged = cache.purge_mismatched()
        print(f"purged {len(purged)} entry(ies)")
        for problem in purged:
            print(f"  - {problem['trigger']}  ->  {problem['tools']}  ({problem['reason']})")
        print()

    if "--audit" in sys.argv:
        problems = cache.audit()
        print(f"{len(problems)} problem entry(ies)")
        for problem in problems:
            print(f"  ! {problem['trigger']}")
            print(f"      tools : {problem['tools']}")
            print(f"      reason: {problem['reason']}")
        print()

    entries = cache._all_with_payload()
    print(f"{len(entries)} active entry(ies)\n")
    for entry in entries:
        payload = entry.get("payload") or {}
        if entry.get("kind") == "plan":
            action = " -> ".join(str(s.get("tool")) for s in payload.get("steps") or [])
        else:
            action = f"{payload.get('tool')}({json.dumps(payload.get('args') or {})})"
        print(f"  {entry['trigger']}")
        print(f"      {entry['kind']}: {action}")


if __name__ == "__main__":
    main()
