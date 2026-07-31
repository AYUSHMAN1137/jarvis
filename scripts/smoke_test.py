"""End-to-end smoke test against a running J.A.R.V.I.S server.

Sends real chat messages through the streaming endpoint and prints the final
reply for each, so a change can be validated the way a user experiences it.

    python scripts/smoke_test.py                 # default safe checks
    python scripts/smoke_test.py "open notepad" "close notepad"
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://localhost:8000"

DEFAULT_MESSAGES = [
    "hello, who are you",
    "what options are there in the settings window",
]


def health() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE}/health", timeout=10) as resp:
            return resp.status == 200
    except Exception as exc:  # noqa: BLE001
        print(f"server not reachable: {exc}")
        return False


def send(message: str, session_id: str) -> tuple[str, list, float]:
    body = json.dumps({"message": message, "session_id": session_id}).encode()
    request = urllib.request.Request(
        f"{BASE}/chat/jarvis/stream", data=body,
        headers={"Content-Type": "application/json"},
    )
    text_parts: list[str] = []
    tools: list[str] = []
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=300) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                event = json.loads(payload)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(event, dict):
                activity = event.get("_activity") or {}
                if activity.get("event") == "tool_call":
                    tools.append(str(activity.get("tool")))
                chunk = event.get("chunk") or event.get("text") or ""
                if chunk:
                    text_parts.append(str(chunk))
            elif isinstance(event, str):
                text_parts.append(event)
    return "".join(text_parts), tools, time.perf_counter() - started


def main() -> None:
    if not health():
        sys.exit(1)
    messages = sys.argv[1:] or DEFAULT_MESSAGES
    session_id = "smoke-" + str(int(time.time()))
    failures = 0
    for index, message in enumerate(messages, 1):
        print(f"\n--- [{index}] {message}")
        try:
            reply, tools, took = send(message, session_id)
        except Exception as exc:  # noqa: BLE001
            print(f"    REQUEST FAILED: {exc}")
            failures += 1
            continue
        print(f"    tools : {', '.join(tools) or '(none)'}")
        print(f"    took  : {took:.1f}s")
        print(f"    reply : {reply.strip()[:400] or '(empty)'}")
        if not reply.strip():
            failures += 1
    print(f"\n{len(messages) - failures}/{len(messages)} produced a reply")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
