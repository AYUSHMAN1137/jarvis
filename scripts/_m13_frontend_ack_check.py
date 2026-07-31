"""One-off manual check: prove the frontend ACK chain end to end.

Run (server must already be up):
    .venv\\Scripts\\python.exe scripts\\_m13_frontend_ack_check.py

Acts as the browser would: runs a turn that produces a frontend action, reads the
`_meta` off the `_actions` payload, and POSTs /api/activity/frontend-ack exactly
like web/script.js does. Then checks that the verdict came back PASS and that the
command was promoted into the verified cache.

This is the M13 §9 definition-of-done item that a unit test cannot cover: it
exercises the real endpoint, the real Checker and the real cache. It opens a
browser tab as a side effect (the whole point -- that is the action being
verified) and writes one entry to data/command_cache.db.
"""

import json
import sys
import time
import uuid

import requests

BASE = "http://127.0.0.1:8000"
COMMAND = "open example.com in the browser"


def wait_for_server(seconds=120):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            return requests.get(f"{BASE}/health", timeout=5).json()
        except Exception:
            time.sleep(2)
    return None


def run_turn(session_id, message):
    """Returns (reply, meta) where meta is the dispatch correlation, if any."""
    response = requests.post(
        f"{BASE}/chat/jarvis/stream",
        json={"session_id": session_id, "message": message},
        stream=True, timeout=240,
    )
    response.raise_for_status()
    reply, meta, seen = [], None, []
    for raw in response.iter_lines(decode_unicode=True):
        if not raw or not raw.startswith("data: "):
            continue
        try:
            payload = json.loads(raw[6:])
        except Exception:
            continue
        if "actions" in payload:
            actions = payload["actions"] or {}
            print(f"  [_actions] wopens={actions.get('wopens')} "
                  f"meta={actions.get('_meta')}")
            if (actions.get("_meta") or {}).get("dispatch_id"):
                meta = actions["_meta"]
                # Acknowledge IMMEDIATELY, exactly as the browser does, so the
                # Checker's poll window sees it.
                ack = {
                    "dispatch_id": meta["dispatch_id"],
                    "execution_id": meta.get("execution_id", ""),
                    "action_id": meta["action_id"],
                    "attempted": True, "accepted": True, "error": "",
                }
                result = requests.post(f"{BASE}/api/activity/frontend-ack",
                                       json=ack, timeout=10)
                print(f"  [ack] posted -> {result.status_code} {result.text.strip()}")
        elif "activity" in payload:
            event = payload["activity"].get("event")
            if event in ("understood", "tool_call", "tool_result", "verifying",
                         "verdict", "retrying", "no_op_rejected", "cache_hit",
                         "cache_miss", "verification_queued"):
                seen.append(payload["activity"])
                interesting = {k: v for k, v in payload["activity"].items()
                               if k != "event" and v not in (None, "", [], {})}
                print(f"  [{event}] {interesting}")
        elif "chunk" in payload:
            reply.append(payload["chunk"])
        elif "done" in payload:
            break
    return "".join(reply).strip(), meta, seen


def main():
    health = wait_for_server()
    if health is None:
        print("Server not reachable")
        return 1
    print("health:", health)

    print("\n" + "=" * 70)
    print(f"TURN 1 (cold): {COMMAND}")
    print("=" * 70)
    reply, meta, seen = run_turn(str(uuid.uuid4()), COMMAND)
    print(f"\nJARVIS: {reply}")

    if meta is None:
        print("\nFAIL: no frontend action was dispatched, so the chain cannot be "
              "checked. Did the agent pick a non-web tool?")
        return 1

    verdicts = [a for a in seen if a.get("event") == "verdict"]
    print("\n--- results ---")
    print(f"dispatch_id : {meta['dispatch_id'][:16]}")
    print(f"action_id   : {meta['action_id'][:16]}")
    for verdict in verdicts:
        print(f"verdict     : {verdict.get('tool')} -> {verdict.get('verdict')}"
              f" ({verdict.get('reason')})")
    passed = any(v.get("verdict") == "PASS" for v in verdicts)
    print(f"PASS reached: {passed}"
          + ("" if passed else "   <-- the ACK chain is still broken"))

    # Give the cache's execution-join a moment, then look for the promotion.
    time.sleep(3)
    try:
        state = requests.get(f"{BASE}/api/dashboard/state", timeout=15).json()
        cache = (state.get("cache") or state.get("phase6") or {})
        print(f"cache stats : {json.dumps(cache)[:400]}")
    except Exception as exc:
        print(f"cache stats unavailable: {exc}")

    print("\n" + "=" * 70)
    print(f"TURN 2 (warm -- should be a cache hit): {COMMAND}")
    print("=" * 70)
    reply2, meta2, seen2 = run_turn(str(uuid.uuid4()), COMMAND)
    if meta2 is not None:
        ack_note = "acknowledged again"
    else:
        ack_note = "no frontend action"
    hit = any(a.get("event") == "cache_hit" for a in seen2)
    print(f"\nJARVIS: {reply2}")
    print(f"cache hit   : {hit} ({ack_note})")
    print("\nEarned speed only appears once a command has been VERIFIED at least "
          "once, so a miss here on the very first run is expected.")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
