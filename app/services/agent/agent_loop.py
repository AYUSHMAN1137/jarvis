"""
The agentic ReAct loop — the brain that replaces the old hardcoded task system.

Flow (per user message):
  1. Send the message + the full list of registered tools to the LLM.
  2. The LLM decides which tool(s) to call (it can call several).
  3. We execute each tool and feed the results back.
  4. Repeat until the LLM stops calling tools and gives a final answer,
     or until AGENT_MAX_STEPS is reached.

Reliability features:
  - Native OpenAI-style tool-calling (no fragile regex).
  - Provider failover order (NEVER raced): GLM-4.7-Flash (Z.ai) primary ->
    gpt-oss (Groq) -> Gemini.
  - Multi-key rotation + per-key circuit breaker for every provider.
  - Dangerous tools (shutdown, delete, etc.) pause and ask the user to confirm.
  - Frontend actions (open URL, play, image, written content) are collected via
    a thread-local sink and returned for the chat layer to emit.
  - Hard step cap so it can never loop forever.

Switching to a local LLM later: set AGENT_BASE_URL to your OpenAI-compatible
endpoint and AGENT_MODEL to the local model. Nothing else changes.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Tuple

from config import (
    GROQ_API_KEYS,
    AGENT_MODEL,
    AGENT_BASE_URL,
    AGENT_MAX_STEPS,
    AGENT_REQUEST_TIMEOUT,
    GEMINI_MODEL,
    GEMINI_REQUEST_TIMEOUT,
    ZAI_MODEL,
    ZAI_REQUEST_TIMEOUT,
)
from app.services.agent.tool_registry import registry
from app.services.agent import action_sink
from app.services import llm_providers
from app.services.api_key_monitor import get_api_key_monitor

logger = logging.getLogger("J.A.R.V.I.S")

_AGENT_SYSTEM_PROMPT = """You are J.A.R.V.I.S, an AI assistant that controls the user's Windows computer and browser through tools.

You decide which tools to call to fulfil the user's request. You may call several tools, one after another, to complete multi-step tasks (for example: open an app, then type into it; or open a website, then interact with it).

Rules:
- Use tools to perform any real action. Do not pretend an action happened.
- For desktop apps (notepad, calculator, chrome, etc.) use open_application. For web addresses use open_website.
- After a tool returns, read its result. If it starts with "ERROR", adapt: try a different tool or fix the arguments. Do not repeat the exact same failing call.
- When a step needs a window to be focused before typing, focus it first.
- Keep going until the user's request is fully done, then give a short, natural, spoken-style final reply (1-2 sentences). No markdown, no emojis.
- If the request is impossible with the available tools, say so briefly and honestly.
- Be efficient: don't call tools that aren't needed."""


@dataclass
class AgentResult:
    """Final result of an agent run, used when not streaming."""
    text: str = ""
    actions: Dict[str, Any] = field(default_factory=dict)
    steps: int = 0
    pending_confirmation: Optional[Dict[str, Any]] = None


class AgentLoop:
    def __init__(self) -> None:
        self._clients = self._build_clients()
        self.last_provider_event = None
        # Phase 3: last-built context registry (live system state + recent
        # results). Tools can read it to resolve references like "this/isko".
        # Rebuilt on every run; None until the first message is processed.
        self._registry = None

        # Z.ai/GLM was unreliable (kept failing), so it has been REMOVED from the
        # agent. Provider order is now: Gemini 2.5 Flash (primary) -> gpt-oss via
        # Groq (fallback). Left disabled (empty) rather than deleted so it can be
        # re-enabled later if needed.
        self._zai_clients = []

        # Optional Gemini fallback clients (raw OpenAI SDK -> Gemini endpoint).
        # NOTE: the agent NEVER races providers — tool-calling must stay
        # deterministic, so Gemini is strictly a failover after Groq.
        self._gemini_clients = []
        if llm_providers.gemini_enabled():
            try:
                self._gemini_clients = [
                    llm_providers.make_raw_client(idx)
                    for idx in range(llm_providers.key_count())
                ]
                logger.info("[AGENT] Gemini fallback clients ready: %d", len(self._gemini_clients))
            except Exception as e:  # noqa: BLE001
                logger.warning("[AGENT] Could not init Gemini fallback clients: %s", e)
                self._gemini_clients = []

        if not self._gemini_clients and not self._clients:
            raise ValueError(
                "No agent LLM providers configured: set GEMINI_API_KEY (primary) "
                "and/or GROQ_API_KEY (fallback)."
            )

        primary = f"Gemini/{GEMINI_MODEL}" if self._gemini_clients else f"Groq/{AGENT_MODEL}"
        logger.info(
            "[AGENT] Loop initialized | primary=%s | tools=%d | gemini_keys=%d | groq_keys=%d",
            primary, len(registry.names()), len(self._gemini_clients), len(self._clients),
        )

    def _build_clients(self) -> List[Any]:
        from groq import Groq
        clients = []
        for key in GROQ_API_KEYS:
            if AGENT_BASE_URL:
                clients.append(Groq(api_key=key, base_url=AGENT_BASE_URL))
            else:
                clients.append(Groq(api_key=key))
        return clients

    # -- low-level LLM call with provider failover ----------------------- #
    # Provider order is FIXED and NEVER raced (tool-calling must stay
    # deterministic):
    #   1) PRIMARY  : Gemini 2.5 Flash (best tool-calling)
    #   2) FALLBACK : gpt-oss via Groq
    def _chat_completion(self, messages: List[Dict[str, Any]], key_index: int):
        tools = registry.openai_schemas()

        # 1) PRIMARY: Gemini (OpenAI-compatible endpoint).
        completion = self._gemini_chat_completion(messages, tools, failover=False)
        if completion is not None:
            return completion

        # 2) FALLBACK: gpt-oss via Groq.
        completion = self._groq_chat_completion(messages, tools, key_index)
        if completion is not None:
            return completion

        raise RuntimeError("All agent LLM providers failed (Gemini, Groq).")

    def _zai_chat_completion(self, messages: List[Dict[str, Any]], tools):
        """PRIMARY: native tool-calling via GLM on Z.ai's OpenAI-compatible API."""
        if not self._zai_clients:
            return None
        monitor = get_api_key_monitor()
        for idx in llm_providers.zai_ordered_keys():
            if idx >= len(self._zai_clients):
                continue
            monitor.record_provider_attempt("zai", operation="agent_chat", source="agent_loop")
            try:
                completion = self._zai_clients[idx].chat.completions.create(
                    model=ZAI_MODEL,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    temperature=0.3,
                    max_tokens=1024,
                    timeout=ZAI_REQUEST_TIMEOUT,
                )
                monitor.record_provider_success("zai", operation="agent_chat", source="agent_loop")
                self._set_provider_event("zai", idx, failover=False)
                logger.info("[AGENT] GLM (Z.ai) completion on key #%d", idx + 1)
                return completion
            except Exception as e:  # noqa: BLE001
                monitor.record_provider_failure("zai", operation="agent_chat", source="agent_loop", error=str(e))
                llm_providers.zai_trip(idx)
                logger.warning("[AGENT] Z.ai (GLM) key #%d failed: %s", idx + 1, str(e)[:120])
                continue
        logger.warning("[AGENT] All Z.ai (GLM) keys failed -> failing over to Groq")
        return None

    def _groq_chat_completion(self, messages: List[Dict[str, Any]], tools, key_index: int):
        """FALLBACK 1: native tool-calling via gpt-oss on Groq, with key rotation."""
        if not self._clients:
            return None
        n = len(self._clients)
        last_exc = None
        monitor = get_api_key_monitor()
        # Gemini is the primary, so any Groq answer is a failover (UI badge).
        groq_is_failover = bool(self._gemini_clients)
        for j in range(n):
            i = (key_index + j) % n
            monitor.record_groq_attempt(i, operation="agent_chat", source="agent_loop")
            t0 = time.perf_counter()
            try:
                completion = self._clients[i].chat.completions.create(
                    model=AGENT_MODEL,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    temperature=0.3,
                    max_tokens=1024,
                    timeout=AGENT_REQUEST_TIMEOUT,
                )
                monitor.record_groq_success(i, operation="agent_chat", source="agent_loop", latency_ms=int((time.perf_counter() - t0) * 1000))
                self._set_provider_event("groq", i, failover=(groq_is_failover or j > 0))
                return completion
            except Exception as e:  # noqa: BLE001
                last_exc = e
                msg = str(e).lower()
                is_rl = "429" in str(e) or "rate limit" in msg
                # Malformed tool-call from the model (gpt-oss/llama sometimes glue
                # the tool name + JSON args together -> HTTP 400). This is a model
                # output bug, NOT a key problem: every Groq key on the same model
                # produces the same broken call, so retrying them all just wastes
                # time. Stop early and fail over to Gemini (reliable tool-calling).
                is_tool_validation = (
                    "tool call validation failed" in msg
                    or "tool_use_failed" in msg
                    or ("400" in str(e) and "tool" in msg)
                )
                monitor.record_groq_failure(i, operation="agent_chat", source="agent_loop", error=str(e), is_rate_limit=is_rl, latency_ms=int((time.perf_counter() - t0) * 1000))
                if is_tool_validation:
                    logger.warning("[AGENT] Groq key #%d: malformed tool call from model -> failing over to Gemini immediately", i)
                    break
                if is_rl:
                    logger.warning("[AGENT] Groq key #%d rate limited, trying next", i)
                    continue
                logger.warning("[AGENT] Groq key #%d error: %s", i, str(e)[:120])
                continue
        if last_exc is not None:
            logger.warning("[AGENT] All Groq keys failed -> failing over to Gemini")
        return None

    def _gemini_chat_completion(self, messages: List[Dict[str, Any]], tools, failover: bool = True):
        """PRIMARY: native tool-calling via Gemini's OpenAI-compatible endpoint."""
        if not self._gemini_clients:
            return None
        monitor = get_api_key_monitor()
        for idx in llm_providers.ordered_keys():
            if idx >= len(self._gemini_clients):
                continue
            monitor.record_gemini_attempt(idx, operation="agent_chat", source="agent_loop")
            t0 = time.perf_counter()
            try:
                completion = self._gemini_clients[idx].chat.completions.create(
                    model=GEMINI_MODEL,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    temperature=0.3,
                    max_tokens=1024,
                    timeout=GEMINI_REQUEST_TIMEOUT,
                )
                monitor.record_gemini_success(idx, operation="agent_chat", source="agent_loop", latency_ms=int((time.perf_counter() - t0) * 1000))
                self._set_provider_event("gemini", idx, failover=failover)
                logger.info("[AGENT] Gemini %s completion on key #%d", "fallback" if failover else "primary", idx + 1)
                return completion
            except Exception as e:  # noqa: BLE001
                monitor.record_gemini_failure(idx, operation="agent_chat", source="agent_loop", error=str(e), is_rate_limit=llm_providers.is_rate_limit_error(e), latency_ms=int((time.perf_counter() - t0) * 1000))
                llm_providers.trip(idx)
                logger.warning("[AGENT] Gemini key #%d failed: %s", idx + 1, str(e)[:120])
                continue
        return None

    def _set_provider_event(self, provider: str, key_index: int, failover: bool = False) -> None:
        """Record which provider/key produced the agent completion (for the UI)."""
        if provider == "gemini":
            label = "GEMINI_API_KEY" if key_index == 0 else f"GEMINI_API_KEY_{key_index + 1}"
            pretty = "Gemini"
        elif provider == "zai":
            label = "ZAI_API_KEY" if key_index == 0 else f"ZAI_API_KEY_{key_index + 1}"
            pretty = "GLM"
        else:
            label = "GROQ_API_KEY" if key_index == 0 else f"GROQ_API_KEY_{key_index + 1}"
            pretty = "Groq"
        self.last_provider_event = {
            "event": "provider_failover" if failover else "llm_provider",
            "provider": provider,
            "key_index": key_index,
            "key_label": label,
            "operation": "agent_chat",
            "failover": bool(failover),
            "message": f"Agent \u2192 {pretty} ({label})" + (" (failover)" if failover else ""),
            "route": "task",
        }

    @staticmethod
    def _parse_tool_calls(message) -> List[Tuple[str, str, Dict[str, Any]]]:
        """Return list of (call_id, name, arguments)."""
        calls = []
        for tc in (getattr(message, "tool_calls", None) or []):
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:  # noqa: BLE001
                args = {}
            calls.append((tc.id, name, args))
        return calls

    @staticmethod
    def _assistant_msg_with_calls(message) -> Dict[str, Any]:
        return {
            "role": "assistant",
            "content": message.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in (getattr(message, "tool_calls", None) or [])
            ],
        }

    def _build_state_block(self, chat_history: Optional[List[tuple]]) -> str:
        """Phase 3: compact live system-state block for the agent prompt.

        Combines Watcher state (active window, open apps, clipboard, toggles),
        recent conversation (mention boost) and the last action target into a
        ContextRegistry, then renders a short block the model can use to resolve
        vague references and fill exact tool arguments.

        Fully fail-soft: ANY problem -> returns "" so the agent behaves exactly
        as before. This is the fix for the "the part that does the work is
        blind" gap (the tool path never saw memory or live state).
        """
        try:
            from app.services.context.context_engine import (
                build_registry, get_alias_store, set_active_registry,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("[AGENT] context engine unavailable: %s", e)
            return ""
        state = None
        try:
            from app.services.watcher import get_watcher
            state = get_watcher().get_state()
        except Exception as e:  # noqa: BLE001
            logger.debug("[AGENT] watcher state unavailable: %s", e)
        last_action = None
        try:
            from app.services.memory_service import get_memory
            mem = get_memory()
            fn = getattr(mem, "_last_action", None)
            if callable(fn):
                res = fn()
                if res and len(res) >= 2 and res[1]:
                    last_action = (res[0], res[1])
        except Exception as e:  # noqa: BLE001
            logger.debug("[AGENT] last action unavailable: %s", e)
        alias_store = None
        try:
            alias_store = get_alias_store()
        except Exception:  # noqa: BLE001
            alias_store = None
        try:
            reg = build_registry(
                state=state, conversation=chat_history, last_action=last_action,
                alias_store=alias_store,
            )
            self._registry = reg  # expose for tools (Phase 3 resolve)
            set_active_registry(reg)  # thread-local handoff to desktop tools
            block = reg.format_state_block()
            # Phase 4: append last verification verdict + any honest learner note.
            try:
                from app.services.agent.phase4 import get_phase4
                note = get_phase4().state_note()
                if note:
                    block = (block + "\n" + note) if block else note
            except Exception:
                pass
            return block
        except Exception as e:  # noqa: BLE001
            logger.debug("[AGENT] state block build failed: %s", e)
            return ""

    def _build_messages(
        self, user_message: str, chat_history: Optional[List[tuple]]
    ) -> List[Dict[str, Any]]:
        messages: List[Dict[str, Any]] = [{"role": "system", "content": _AGENT_SYSTEM_PROMPT}]
        # Phase 3: inject the live system-state block (if available) so the
        # tool-calling brain is no longer blind to what's actually on screen.
        state_block = self._build_state_block(chat_history)
        if state_block:
            messages.append({"role": "system", "content": state_block})
        for u, a in (chat_history or [])[-6:]:
            messages.append({"role": "user", "content": str(u)})
            messages.append({"role": "assistant", "content": str(a)})
        messages.append({"role": "user", "content": user_message})
        return messages

    # -- streaming run --------------------------------------------------- #
    def run_stream(
        self,
        user_message: str,
        chat_history: Optional[List[tuple]] = None,
        key_index: int = 0,
        confirmed_tools: Optional[List[str]] = None,
    ) -> Iterator[Dict[str, Any]]:
        """Yields event dicts:
          {"_activity": {...}}           progress for the UI
          {"_actions": {...}}            frontend actions (open/play/image/...)
          {"_confirm": {...}}            dangerous action awaiting confirmation
          {"_final": "text"}             final spoken answer
        """
        action_sink.reset()
        confirmed = set(confirmed_tools or [])
        messages = self._build_messages(user_message, chat_history)
        tool_result_order = 0  # Phase 3: ordinal sequence for tool results

        yield {"_activity": {"event": "agent_started", "tools": len(registry.names())}}

        final_text = ""
        last_provider_sig = None
        for step in range(1, AGENT_MAX_STEPS + 1):
            try:
                completion = self._chat_completion(messages, key_index)
            except Exception as e:  # noqa: BLE001
                logger.error("[AGENT] LLM call failed: %s", e)
                final_text = "I ran into a problem reaching my reasoning engine. Please try again."
                break

            # Surface which provider/key answered (only when it changes).
            ev = getattr(self, "last_provider_event", None)
            if ev:
                sig = (ev.get("provider"), ev.get("key_index"), ev.get("failover"))
                if sig != last_provider_sig:
                    last_provider_sig = sig
                    yield {"_activity": ev}

            choice = completion.choices[0]
            message = choice.message
            tool_calls = self._parse_tool_calls(message)

            if not tool_calls:
                final_text = (message.content or "").strip() or "Done."
                break

            # Append the assistant's tool-call message before adding results.
            messages.append(self._assistant_msg_with_calls(message))

            for call_id, name, args in tool_calls:
                # Dangerous-action gate.
                if registry.is_dangerous(name) and name not in confirmed:
                    yield {
                        "_confirm": {
                            "tool": name,
                            "arguments": args,
                            "message": f"This will run a sensitive action ({name}). Confirm?",
                            "original_message": user_message,
                        }
                    }
                    yield {"_activity": {"event": "awaiting_confirmation", "tool": name}}
                    # Stop here; the chat layer will re-run with confirmed_tools.
                    return

                yield {"_activity": {"event": "tool_call", "tool": name, "args": args, "step": step}}
                observation = registry.execute(name, args)
                try:
                    from app.services.memory_service import get_memory
                    get_memory().record_action(name, args, not str(observation).startswith("ERROR"))
                except Exception:
                    pass
                # Phase 4: publish action.done (non-blocking, fail-soft) so the
                # background Checker can verify the result. NEVER blocks the loop.
                try:
                    from app.services.agent.phase4 import get_phase4
                    get_phase4().publish_action_done(
                        tool=name, args=args, observation=observation,
                        user_message=user_message,
                    )
                except Exception:
                    pass
                # Phase 3: feed successful results back into the live registry so
                # later steps can resolve "pehla wala / jo abhi nikla". Fail-soft.
                try:
                    if self._registry is not None and not str(observation).startswith("ERROR"):
                        tool_result_order += 1
                        self._registry.add_tool_result(name, observation, order=tool_result_order)
                except Exception:
                    pass
                yield {"_activity": {"event": "tool_result",
                                     "tool": name,
                                     "ok": not observation.startswith("ERROR"),
                                     "preview": observation[:120]}}
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": observation,
                })

            # Emit any frontend actions accumulated so far as soon as we have them.
            if action_sink.has_actions():
                yield {"_actions": action_sink.collect()}

        else:
            # Loop exhausted without a final message.
            final_text = "I've done as much as I can on that. Let me know if you'd like more."

        # Final flush of actions (in case the last step produced some).
        if action_sink.has_actions():
            yield {"_actions": action_sink.collect()}

        yield {"_activity": {"event": "agent_done", "steps": step}}
        yield {"_final": final_text}
