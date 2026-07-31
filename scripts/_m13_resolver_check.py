"""One-off manual check: replay session 94bf07c2 through the real resolver.

Run:  .venv\\Scripts\\python.exe scripts\\_m13_resolver_check.py

Makes real LLM calls (read-only, no tools, no actions). It exists to prove the
prompt works against the live models, which a fake-LLM unit test cannot.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.resolver import get_resolver  # noqa: E402

TURNS = [
    ("Open YouTube.", None),
    ("Play ishq song.", None),
    ("It's not playing.", {"tool": "play_on_youtube", "args": {"query": "ishq"},
                           "verdict": "UNKNOWN",
                           "reason": "browser never acknowledged the action"}),
    ("It's a Pakistani song.", None),
    ("Bai Fahim Abdullah.", None),
    ("Search for it.", None),
]

EXTRA = [
    ("YouTube kholo", None, None),
    ("kitna volume hai", None, None),
    ("haan kar do", None, {"tool": "delete_file",
                           "original_message": "delete old.txt"}),
    ("nahi rehne do", None, {"tool": "delete_file",
                             "original_message": "delete old.txt"}),
    ("what am I holding", None, None),
    ("what options are on this page", None, None),
    ("who won the match last night", None, None),
    ("send that email", None, None),
]


def show(label, utterance, result):
    print(f"\n--- {label}: {utterance!r}")
    print(f"    goal            : {result.goal}")
    print(f"    kind            : {result.kind}"
          + (f" / {result.visual_source}" if result.visual_source else ""))
    print(f"    self_contained  : {result.self_contained}")
    print(f"    refers_to_prev  : {result.refers_to_previous}")
    print(f"    is_confirmation : {result.is_confirmation}")
    print(f"    unresolved      : {result.unresolved}")
    print(f"    source/provider : {result.source}/{result.provider} "
          f"({result.elapsed_ms}ms)")


def main():
    resolver = get_resolver()
    if not resolver.available:
        print("No LLM providers configured; nothing to check.")
        return 1

    history = []
    print("=" * 70)
    print("Session 94bf07c2 replay")
    print("=" * 70)
    for index, (utterance, last_action) in enumerate(TURNS, start=1):
        result = resolver.resolve(utterance, chat_history=history,
                                  last_action=last_action)
        show(f"turn {index}", utterance, result)
        history.append((utterance, "(assistant reply)"))
    print("\n" + "=" * 70)
    print("Assorted single turns")
    print("=" * 70)
    for utterance, hist, pending in EXTRA:
        result = resolver.resolve(utterance, chat_history=hist or [],
                                  confirmation_pending=pending)
        show("single", utterance, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
