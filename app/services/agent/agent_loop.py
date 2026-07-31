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
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Tuple

from config import (
    GROQ_API_KEYS,
    AGENT_MODEL,
    AGENT_BASE_URL,
    AGENT_MAX_OUTPUT_TOKENS,
    AGENT_MAX_STEPS,
    AGENT_NO_OP_NUDGES,
    AGENT_REASONING_EFFORT,
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
from app.services.debug_logger import dbg

logger = logging.getLogger("J.A.R.V.I.S")

_AGENT_SYSTEM_PROMPT = """You are J.A.R.V.I.S, an AI assistant that controls the user's Windows computer and browser through tools.

You decide which tools to call to fulfil the user's request. You may call several tools, one after another, to complete multi-step tasks.

Rules:
- Use tools to perform any real action. Do not pretend an action happened.
- For desktop apps (notepad, calculator, chrome, settings, etc.) use open_application. For web addresses use open_website.
- After a tool returns, read its result. If it starts with "ERROR", adapt: try a different tool or fix the arguments. Do not repeat the exact same failing call.
- A very large result is saved to a file and replaced by its first lines plus the path. Those lines are usually all you need; call read_file with that exact path only if you genuinely need the rest.
- When a step needs a window to be focused before typing, focus it first.
- Keep going until the user's request is FULLY done, then give a short, natural, spoken-style final reply (1-2 sentences). No markdown, no emojis.
- If the request is impossible with the available tools, say so briefly and honestly.
- Be efficient: don't call tools that aren't needed.

UI INTERACTION — CRITICAL:
Opening a window is only step 1. If the user wants something DONE inside it (press a button, flip a switch, type in a field, pick an option), you MUST carry on until that thing is actually done. Opening Settings is not "turning night light off".

Use ui_do for this. It is the main UI tool and it does the searching for you:

  ui_do(target="Night light", action="toggle", state=false, window="Settings")
  ui_do(target="Check for updates", action="click", window="Settings")
  ui_do(target="File name", action="type", text="poem.txt", window="Save as")

ui_do reads the window, and if the control is not on screen yet it uses the window's own search box, opens the matching section, or scrolls — then acts and reports what it observed. You do not need to know where the control lives.

Supporting tools:
- ui_list_controls(window) — see exactly what is on screen with current on/off states. Use it to answer "what options are there", or to get real names after a failure.
- ui_click / ui_set_toggle / ui_type_into — single-shot versions for a control you can already see.
- ui_scroll(window, direction) — bring controls below the fold into reach.
- ui_diagnostics() — use only if several UI actions fail in a row; it reports whether UI automation is alive and which windows exist.

Rules that matter:
- Read the result. It tells you the truth. "ERROR: ..." means it did NOT happen, and the message lists the control names that ARE present — use those names on your next attempt instead of guessing a new one.
- Never claim a toggle changed unless the result says so. ui_do re-reads the control and will tell you if it did not take effect.
- The window argument is a WINDOW TITLE, not a page name. Settings pages (Display, Bluetooth, Night light, Windows Update) all live in the window titled "Settings".
- Settings deep links open the right page in one step: open_settings_page("night light"), open_settings_page("bluetooth"), open_settings_page("windows update").
- If a window was just opened, give it a moment (ui_wait) before expecting content.

BROWSER / WEB PAGE INTERACTION:
Web pages are just another window, so the same tools work:
  1. open_website(url) or play_on_youtube(query) to load the page.
  2. ui_wait(3) — pages need a few seconds.
  3. ui_do(target="<the link or button text>", window="Chrome") to click it.
     If the browser is not Chrome, try "Edge", "Firefox", "Brave" or "Opera".
  4. If you do not know the exact text, ui_list_controls(window="Chrome") first.

Example — "play Arijit Singh on YouTube":
  play_on_youtube("Arijit Singh") → opens the results page
  ui_wait(3)
  ui_list_controls(window="Chrome") → shows the video links
  ui_do(target="<first video title>", window="Chrome") → the video starts
  Then reply briefly, naming what is playing.

Always: open → wait → act on a real control name → confirm from the result.

PREFER A DIRECT TOOL OVER DRIVING A DIALOG:
Some jobs have a tool that does them properly in one call. Use it instead of typing into GUI dialogs, which is slow and easy to get wrong.
- To save text to a file, use write_file(path, content). For example write_file("desktop/poem.txt", "<the poem>"). It understands desktop/downloads/documents by name and puts the file in the user's own folder. Do NOT open Notepad, paste, press Ctrl+S and type a path — that is what fails.
- To read a file use read_file, to make a folder use create_folder, to move or rename use move_path.
- Drive the UI with ui_* only when there is no direct tool for the job."""


@dataclass
class AgentResult:
    """Final result of an agent run, used when not streaming."""
    text: str = ""
    actions: Dict[str, Any] = field(default_factory=dict)
    steps: int = 0
    pending_confirmation: Optional[Dict[str, Any]] = None


# M13 §3.3: the structural no-op gate. This is deliberately a control-flow rule
# in code, not a line in the system prompt: a model that ignores prose (and they
# do -- "Play ishq song." produced the final text "Done." with zero tool calls)
# cannot ignore a branch that refuses to accept its answer.
# Output budget for a one-sentence composed reply. Generous on purpose: the agent
# model is a thinking model and draws its reasoning from the same budget, so a
# tight cap produced truncated admissions like "I attempted" -- which reads as a
# success claim, the exact failure this milestone removes.
_COMPOSE_MAX_TOKENS = 800

_NO_OP_NUDGE = (
    "You did not call any tool, so nothing has happened yet and the user's "
    "request is NOT done. You are not allowed to report success for an action "
    "you did not perform. Either call the tool that actually performs it now, "
    "or -- if it genuinely cannot be done with the tools you have -- say plainly "
    "that you cannot do it and why. Do not say 'Done'."
)


class AgentLoop:
    def __init__(self) -> None:
        self._clients = self._build_clients()
        self.last_provider_event = None
        # Set to False the first time an endpoint rejects `reasoning_effort`, so a
        # model that does not accept it can never cause a total outage.
        self._reasoning_effort = AGENT_REASONING_EFFORT or ""
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
    @staticmethod
    def _is_usable(completion) -> bool:
        """A completion with neither text nor a tool call is not a decision.

        A reasoning model that spends its whole output budget thinking returns an
        empty message. Accepting that as the model's final answer is how a turn
        ends up reporting nothing, or "Done.", having done nothing -- seen live on
        "what's my battery level", where Gemini returned content='' and no tool
        calls twice in a row. It is a provider failure, so it fails over.
        """
        try:
            message = completion.choices[0].message
        except Exception:  # noqa: BLE001
            return False
        if getattr(message, "tool_calls", None):
            return True
        return bool((getattr(message, "content", None) or "").strip())

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
                    max_tokens=AGENT_MAX_OUTPUT_TOKENS,
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
                    max_tokens=AGENT_MAX_OUTPUT_TOKENS,
                    timeout=AGENT_REQUEST_TIMEOUT,
                )
                if not self._is_usable(completion):
                    monitor.record_groq_failure(
                        i, operation="agent_chat", source="agent_loop",
                        error="empty completion (no text, no tool call)",
                        is_rate_limit=False,
                        latency_ms=int((time.perf_counter() - t0) * 1000))
                    logger.warning("[AGENT] Groq key #%d returned an empty "
                                   "completion -- treating as a failure", i)
                    continue
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
                # "Request too large ... on tokens per minute (TPM): Limit N,
                # Requested M" with M > N is not transient and not key-specific:
                # the request cannot fit in ANY key's per-minute allowance, so
                # every remaining key will return the identical 413. Measured
                # live: 8 keys, 8 identical failures, ~2s wasted.
                # The agent payload is dominated by the tool schemas (~42 KB for
                # 95 tools), so on Groq's free tier the agent fallback simply
                # cannot be used -- say so once, clearly, instead of retrying.
                if "413" in str(e) and "tokens per minute" in msg:
                    logger.error(
                        "[AGENT] Groq cannot serve the agent: the request "
                        "(%d tools) exceeds this account's per-minute token "
                        "allowance. Gemini is the only usable agent provider "
                        "until the Groq tier is raised. %s",
                        len(tools or []), str(e)[:200])
                    break
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
                extra = {}
                if self._reasoning_effort:
                    extra["reasoning_effort"] = self._reasoning_effort
                completion = self._gemini_clients[idx].chat.completions.create(
                    model=GEMINI_MODEL,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    temperature=0.3,
                    max_tokens=AGENT_MAX_OUTPUT_TOKENS,
                    timeout=GEMINI_REQUEST_TIMEOUT,
                    **extra,
                )
                if not self._is_usable(completion):
                    monitor.record_gemini_failure(
                        idx, operation="agent_chat", source="agent_loop",
                        error="empty completion (no text, no tool call)",
                        is_rate_limit=False,
                        latency_ms=int((time.perf_counter() - t0) * 1000))
                    logger.warning("[AGENT] Gemini key #%d returned an empty "
                                   "completion -- treating as a failure", idx + 1)
                    continue
                monitor.record_gemini_success(idx, operation="agent_chat", source="agent_loop", latency_ms=int((time.perf_counter() - t0) * 1000))
                self._set_provider_event("gemini", idx, failover=failover)
                logger.info("[AGENT] Gemini %s completion on key #%d", "fallback" if failover else "primary", idx + 1)
                return completion
            except Exception as e:  # noqa: BLE001
                # An endpoint that does not know `reasoning_effort` would reject
                # every key identically, so drop the parameter and retry this key
                # rather than failing the whole run over an optional hint.
                # An endpoint that does not know `reasoning_effort` rejects every
                # key identically, so drop the parameter for the rest of this
                # process and carry on down the key list, rather than failing the
                # whole run over an optional hint. The key is not tripped: it is
                # the request that was wrong, not the credential.
                if self._reasoning_effort and "reasoning_effort" in str(e).lower():
                    logger.warning("[AGENT] endpoint rejected reasoning_effort=%r "
                                   "-- dropping it for this process",
                                   self._reasoning_effort)
                    self._reasoning_effort = ""
                    continue
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

    def _build_state_block(self, user_message: str,
                           chat_history: Optional[List[tuple]]) -> str:
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
            block = reg.format_state_block(query=user_message)
            # Phase 4: append last verification verdict + any honest learner note.
            try:
                from app.services.agent.checker import get_phase4
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
        state_block = self._build_state_block(user_message, chat_history)
        if state_block:
            dbg.state_block(state_block)
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
        expect_action: bool = False,
    ) -> Iterator[Dict[str, Any]]:
        """Yields event dicts:
          {"_activity": {...}}           progress for the UI
          {"_actions": {...}}            frontend actions (open/play/image/...)
          {"_confirm": {...}}            dangerous action awaiting confirmation
          {"_executed": [...]}           the actions this run really performed
          {"_final": "text"}             final spoken answer

        `expect_action=True` marks this run as one the user expects to CHANGE
        something. A final message with zero tool calls is then rejected instead
        of being recorded as success (M13 §3.3).
        """
        action_sink.reset()
        confirmed = set(confirmed_tools or [])
        messages = self._build_messages(user_message, chat_history)
        tool_result_order = 0  # Phase 3: ordinal sequence for tool results
        execution_id = uuid.uuid4().hex
        executed_steps: List[Dict[str, Any]] = []
        execution_ok = True
        from app.services.agent.execution import (
            ExecutionContext, ExecutionManifest, get_execution_coordinator,
        )
        execution_context = ExecutionContext(
            execution_id=execution_id, user_message=user_message, source="agent"
        )
        execution_actions = []
        execution_results = []
        execution_coordinator = get_execution_coordinator()

        # --- DEBUG LOGGER: agent loop is running (session already started by chat_service) ---
        dbg.ensure_session(session_id="agent_direct", user_message=user_message)
        dbg.info("AGENT", f"run_stream started | max_steps={AGENT_MAX_STEPS} | tools={len(registry.names())}")
        dbg.tool_catalog(registry.names())
        dbg.execution_event("started", execution_id, {"command": user_message[:200]})

        yield {"_activity": {"event": "agent_started", "tools": len(registry.names())}}
        yield {"_activity": {"event": "execution_started",
                             "execution_id": execution_id[:12]}}

        final_text = ""
        last_provider_sig = None
        # A nudge is not a real step, so it gets its own budget rather than
        # eating one of AGENT_MAX_STEPS.
        step = 0
        step_budget = AGENT_MAX_STEPS
        nudges_left = max(0, int(AGENT_NO_OP_NUDGES)) if expect_action else 0
        exhausted = True
        while step < step_budget:
            step += 1
            try:
                dbg.llm_call("gemini/groq", "auto", step=step)
                completion = self._chat_completion(messages, key_index)
            except Exception as e:  # noqa: BLE001
                logger.error("[AGENT] LLM call failed: %s", e)
                dbg.error("AGENT", f"LLM call failed at step {step}: {e}")
                final_text = "I ran into a problem reaching my reasoning engine. Please try again."
                exhausted = False
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
                said = (message.content or "").strip()
                dbg.llm_response("llm", has_tool_calls=False, step=step)
                # --- M13 §3.3: reject the silent no-op ---
                if expect_action and not executed_steps:
                    if nudges_left > 0:
                        nudges_left -= 1
                        step_budget += 1  # the nudge is free
                        messages.append({"role": "assistant", "content": said})
                        messages.append({"role": "system", "content": _NO_OP_NUDGE})
                        dbg.info("AGENT", "NO-OP REJECTED -- nudging", {
                            "said": said[:160], "step": step,
                        })
                        logger.warning(
                            "[AGENT] action turn produced no tool call (%r) -- nudging",
                            said[:80])
                        yield {"_activity": {"event": "no_op_rejected",
                                             "said": said[:120],
                                             "message": "nothing was executed -- retrying"}}
                        continue
                    # Nudged and it still refused to act. Say so; never "Done."
                    final_text = self._no_op_admission(user_message, said)
                    dbg.info("AGENT", "NO-OP ADMITTED", {"final": final_text[:160]})
                    logger.warning("[AGENT] action turn executed nothing -- admitting")
                    exhausted = False
                    break
                final_text = said or "Done."
                dbg.info("AGENT", f"Final answer: {final_text[:200]}")
                exhausted = False
                break

            # Log what the LLM decided to call
            tool_names = [tc[1] for tc in tool_calls]
            dbg.llm_response("llm", has_tool_calls=True, tool_names=tool_names, step=step)
            # Any prose the model emitted alongside the tool calls explains its
            # reasoning -- essential when it picks the wrong tool.
            if (message.content or "").strip():
                dbg.llm_text(message.content, step=step)

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
                action_id = str(call_id or uuid.uuid4().hex)
                action_spec = execution_coordinator.action(
                    name, args, index=len(execution_actions) + 1, action_id=action_id
                )
                action_result = execution_coordinator.execute_action(
                    execution_context, action_spec, confirmation_already_checked=True
                )
                execution_actions.append(action_spec)
                execution_results.append(action_result)
                observation = action_result.observation
                obs_ok = action_result.transport_ok
                executed_steps.append({
                    "action_id": action_id,
                    "tool": name,
                    "args": dict(args or {}),
                })
                execution_ok = execution_ok and obs_ok
                # Phase 3: feed successful results back into the live registry so
                # later steps can resolve "pehla wala / jo abhi nikla". Fail-soft.
                try:
                    if self._registry is not None and obs_ok:
                        tool_result_order += 1
                        self._registry.add_tool_result(name, observation, order=tool_result_order)
                except Exception:
                    pass
                yield {"_activity": {"event": "tool_result",
                                     "tool": name,
                                     "ok": obs_ok,
                                     "preview": observation[:120]}}
                # M13 §3.2: emit exactly THIS action's browser payload. The sink
                # was drained inside execute_action, so each emission is disjoint
                # and carries its own dispatch_id -- previously step 2 re-emitted
                # step 1's URL (opening the same tab twice) and only the last
                # action of a run could ever be acknowledged.
                if action_sink.bucket_has_actions(action_result.frontend_actions):
                    yield {"_actions": dict(action_result.frontend_actions)}
                # An oversized result is written to disk and replaced by a short
                # pointer. It stays in `observation` for the registry, the
                # activity feed and verification above -- only the copy the
                # model has to re-read on every later step is trimmed.
                try:
                    from app.services.agent import tool_result_store
                    content = tool_result_store.maybe_offload(
                        name, observation,
                        execution_id=execution_context.execution_id,
                        action_id=action_id)
                except Exception:  # noqa: BLE001 - never lose a tool result
                    content = observation
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": content,
                })

        if exhausted:
            # Loop exhausted without a final message.
            final_text = "I've done as much as I can on that. Let me know if you'd like more."

        # Safety net: a tool that pushed to the sink outside the coordinator path
        # would otherwise be silently dropped now that draining is per-action.
        if action_sink.has_actions():
            yield {"_actions": action_sink.collect(drain=True)}

        # Cache learning happens at execution level, never from an isolated tool
        # event. Verification is asynchronous, so Phase 6 joins this manifest to
        # the individual verdicts by execution_id/action_id.
        if executed_steps:
            try:
                manifest = ExecutionManifest(
                    context=execution_context, actions=execution_actions,
                    results=execution_results,
                    status="completed" if execution_ok else "failed",
                )
                execution_coordinator.complete(manifest)
                yield {"_activity": {"event": "verification_queued",
                                     "execution_id": execution_id[:12],
                                     "actions": len(executed_steps)}}
            except Exception:
                pass

        dbg.session_end(final_response=final_text)
        yield {"_activity": {"event": "agent_done", "steps": step}}
        # What this run actually did, so the chat layer can wait for the verdicts
        # of exactly these actions before it says anything (M13 §3.4).
        yield {"_executed": {"execution_id": execution_id,
                             "steps": list(executed_steps)}}
        yield {"_final": final_text}

    # -- honest wording, generated rather than templated ------------------ #
    def _compose(self, instruction: str, fallback: str) -> str:
        """Ask the LLM for one short spoken sentence. Falls back to plain words.

        Used wherever JARVIS has to admit a limit or ask for a missing detail. The
        wording is generated so it fits the request instead of reading like a
        canned string, but the fallback keeps it honest when no provider answers.

        A truncated admission is worse than a templated one -- "I attempted" is
        not a sentence, and it reads as a success claim. So the budget is generous,
        thinking is off (the same reasoning-model trap as the main loop), and a
        `finish_reason == "length"` reply is rejected rather than shipped.
        """
        messages = [
            {"role": "system",
             "content": ("You are J.A.R.V.I.S. Reply with ONE short, COMPLETE "
                         "spoken sentence. No markdown, no emojis, no preamble, "
                         "no line breaks. Be honest and specific. Never claim "
                         "anything worked.")},
            {"role": "user", "content": instruction},
        ]
        for client, model, timeout, extra in self._plain_clients():
            try:
                completion = client.chat.completions.create(
                    model=model, messages=messages, temperature=0.3,
                    max_tokens=_COMPOSE_MAX_TOKENS, timeout=timeout, **extra,
                )
                choice = completion.choices[0]
                text = " ".join((choice.message.content or "").split()).strip()
                if not text:
                    logger.debug("[AGENT] compose returned nothing on %s", model)
                    continue
                truncated = (
                    str(getattr(choice, "finish_reason", "")).lower() == "length"
                    # Belt and braces: the Gemini compat layer has been seen to
                    # report finish_reason="stop" on a reply cut short by thinking.
                    # A two-word "sentence" is not usable either way.
                    or len(text.split()) < 4
                )
                if truncated:
                    logger.warning("[AGENT] compose truncated on %s (%r) -- "
                                   "using plain wording instead", model, text[:60])
                    continue
                return text
            except Exception as e:  # noqa: BLE001
                logger.debug("[AGENT] compose failed on %s: %s", model, str(e)[:100])
                continue
        return fallback

    def _plain_clients(self):
        """(client, model, timeout, extra_kwargs) tuples for tool-free completions."""
        out = []
        gemini_extra = ({"reasoning_effort": self._reasoning_effort}
                        if getattr(self, "_reasoning_effort", "") else {})
        for client in self._gemini_clients[:1]:
            out.append((client, GEMINI_MODEL, GEMINI_REQUEST_TIMEOUT, gemini_extra))
        for client in self._clients[:1]:
            out.append((client, AGENT_MODEL, AGENT_REQUEST_TIMEOUT, {}))
        return out

    def _no_op_admission(self, goal: str, said: str) -> str:
        return self._compose(
            "The user asked: \"" + str(goal)[:300] + "\"\n"
            "You called no tool at all, so nothing was performed. "
            + (f"You had drafted this reply, which would have been untrue: \"{said[:200]}\".\n"
               if said else "")
            + "Tell the user in one sentence that you did not manage to do it and "
              "that nothing was changed. Do not apologise more than once. Do not "
              "say 'Done'.",
            "I wasn't able to carry that out — nothing was changed. "
            "Tell me a bit more and I'll try again.",
        )

    def compose_unconfirmed(self, goal: str, draft: str,
                            verdicts: List[Dict[str, Any]]) -> str:
        """Rewrite a draft reply so it states the limit of what was confirmed.

        `verdicts` is a list of {tool, verdict, reason} for the actions this turn
        ran. This is what stops "Opening YouTube for you, Sir" from being said
        when the browser never acknowledged anything.
        """
        lines = []
        for v in verdicts[:4]:
            reason = str(v.get("reason") or "").strip()
            lines.append(f"- {v.get('tool')}: {v.get('verdict')}"
                         + (f" ({reason})" if reason else ""))
        return self._compose(
            "The user asked: \"" + str(goal)[:300] + "\"\n"
            "You were about to say: \"" + str(draft)[:300] + "\"\n"
            "Verification of what you actually did came back as:\n"
            + "\n".join(lines) + "\n"
            "Rewrite your reply in one sentence so it is true: say what you did, "
            "and say plainly that you could not confirm it took effect (or that "
            "it failed, if it failed). Never claim it worked.",
            (str(draft).rstrip(". ") + ". I could not confirm it actually took effect, though.")
            if draft else "I ran that, but I could not confirm it actually took effect.",
        )
