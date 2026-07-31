"""One-off manual check: drive real turns through the running server.

Run (server must already be up):
    .venv\\Scripts\\python.exe scripts\\_m13_live_turn.py

Deliberately uses NON-ACTING messages plus one read-only device question, so it
exercises the full pipeline -- resolver, routing, streaming, activity events --
without changing anything on the machine.
"""

import json
import sys
import uuid

import requests

BASE = "http://127.0.0.1:8000"

TURNS = [
    "hello there",                 # knowledge_question
    "what is a closure in python",  # knowledge_question
    "what's my battery level",      # action (read-only, local device state)
]


def run_turn(session_id, message):
    print("\n" + "=" * 70)
    print(f"USER: {message}")
    print("=" * 70)
    response = requests.post(
        f"{BASE}/chat/jarvis/stream",
        json={"session_id": session_id, "message": message},
        stream=True, timeout=180,
    )
    response.raise_for_status()
    reply = []
    for raw in response.iter_lines(decode_unicode=True):
        if not raw or not raw.startswith("data: "):
            continue
        try:
            payload = json.loads(raw[6:])
        except Exception:
            continue
        if "activity" in payload:
            activity = payload["activity"]
            event = activity.get("event")
            if event in ("understood", "decision", "routing", "tool_call",
                         "tool_result", "verifying", "verdict", "retrying",
                         "no_op_rejected", "cache_miss", "cache_hit",
                         "agent_done", "first_chunk"):
                interesting = {k: v for k, v in activity.items()
                               if k != "event" and v not in (None, "", [], {})}
                print(f"  [{event}] {interesting}")
        elif "chunk" in payload:
            reply.append(payload["chunk"])
        elif "done" in payload:
            break
    print(f"\nJARVIS: {''.join(reply).strip()}")


def wait_for_server(seconds=120):
    import time
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            return requests.get(f"{BASE}/health", timeout=5).json()
        except Exception:
            time.sleep(2)
    return None


def main():
    session_id = str(uuid.uuid4())
    health = wait_for_server()
    if health is None:
        print("Server not reachable")
        return 1
    print("health:", health)
    if not health.get("resolver"):
        print("WARNING: resolver is not wired into app state")
    for message in TURNS:
        run_turn(session_id, message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
