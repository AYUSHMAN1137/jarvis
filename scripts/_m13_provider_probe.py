"""One-off diagnostic: why is the agent's LLM call failing?

Run:  .venv\\Scripts\\python.exe scripts\\_m13_provider_probe.py

Two failures showed up live once empty completions stopped being accepted as
answers:
  * every Gemini key returned an EMPTY message (no text, no tool call)
  * every Groq key returned HTTP 413 "Request too large"

This builds the REAL agent payload (same system prompt, same state block, same
tool schemas) and measures it, so the fix that follows is based on evidence.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from app.services import llm_providers  # noqa: E402
from app.services.agent.tools import load_all_tools  # noqa: E402
from app.services.agent.tool_registry import registry  # noqa: E402

USER = "open example.com in the browser"


def build_real_messages():
    """Exactly what AgentLoop._build_messages produces."""
    from app.services.agent.agent_loop import AgentLoop
    loop = object.__new__(AgentLoop)
    loop._registry = None
    return loop._build_messages(USER, [])


def report(label, completion):
    message = completion.choices[0].message
    calls = [c.function.name for c in (message.tool_calls or [])]
    print(f"\n[{label}]")
    print(f"  finish_reason: {completion.choices[0].finish_reason}")
    print(f"  content      : {(message.content or '')[:90]!r}")
    print(f"  tool_calls   : {calls}")
    print(f"  usage        : {getattr(completion, 'usage', None)}")
    return bool(calls or (message.content or "").strip())


def main():
    load_all_tools()
    tools = registry.openai_schemas()
    messages = build_real_messages()

    tools_bytes = len(json.dumps(tools))
    msg_bytes = len(json.dumps(messages))
    print(f"tools              : {len(tools)}  ({tools_bytes:,} bytes)")
    print(f"messages           : {len(messages)}  ({msg_bytes:,} bytes)")
    for m in messages:
        print(f"   - {m['role']:<9} {len(m.get('content') or ''):>7,} chars")
    print(f"total payload      : {tools_bytes + msg_bytes:,} bytes "
          f"(~{(tools_bytes + msg_bytes) // 4:,} tokens)")
    print(f"agent models       : {config.GEMINI_MODEL} / {config.AGENT_MODEL}")
    print(f"max_output_tokens  : {config.AGENT_MAX_OUTPUT_TOKENS}")

    print("\n" + "=" * 70)
    print("GEMINI (real payload)")
    print("=" * 70)
    client = llm_providers.make_raw_client(0, timeout=90)
    for label, extra in [
        ("current settings", {"max_tokens": config.AGENT_MAX_OUTPUT_TOKENS}),
        ("no max_tokens", {}),
        ("reasoning_effort=none", {"reasoning_effort": "none"}),
    ]:
        try:
            completion = client.chat.completions.create(
                model=config.GEMINI_MODEL, messages=messages, tools=tools,
                tool_choice="auto", temperature=0.3, timeout=90, **extra)
            report(label, completion)
        except Exception as exc:
            print(f"\n[{label}] EXCEPTION: {str(exc)[:400]}")

    print("\n" + "=" * 70)
    print("GROQ (real payload)")
    print("=" * 70)
    from groq import Groq
    groq_client = Groq(api_key=config.GROQ_API_KEYS[0])
    try:
        completion = groq_client.chat.completions.create(
            model=config.AGENT_MODEL, messages=messages, tools=tools,
            tool_choice="auto", temperature=0.3,
            max_tokens=config.AGENT_MAX_OUTPUT_TOKENS, timeout=90)
        report("real payload", completion)
    except Exception as exc:
        print(f"  EXCEPTION: {str(exc)[:800]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
