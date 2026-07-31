"""
The understanding layer (M13 Phase 2).

One LLM call per turn that reads the newest user message **in context** and
answers a single question: *what does the user actually want, said in full?*

It replaces `BrainService.classify_primary` in the hot path, so it costs nothing
extra -- it substitutes for a call that already happened. What it removes is far
larger: five independent hardcoded phrase lists that used to guess at what
English (and Hindi) sentences meant.

Why this exists, from real evidence (session 94bf07c2):

  turn 3  "It's not playing."        -> the retry-complaint phrase list did not
                                        contain "not playing", so a failed action
                                        was answered with small talk.
  turn 6  "Search for it."           -> a regex captured the literal pronoun and
                                        JARVIS searched Google for the word "it".

By turn 6 the conversation contained every fact needed (*Ishq*, Pakistani, Fahim
Abdullah, play, YouTube). Understanding, not matching, is the fix.

Output contract (strict JSON, validated):

    {
      "goal": "play the song Ishq by Fahim Abdullah on YouTube",
      "kind": "action|web_question|knowledge_question|visual|mixed",
      "self_contained": false,
      "refers_to_previous": true,
      "is_confirmation": null,
      "unresolved": [],
      "confidence": 0.0
    }

Discipline:
  * Provider order is FIXED and NEVER raced (Rule #5): Gemini primary, Groq
    fallback. Racing made routing inconsistent when it was tried in the brain.
  * Fail-soft (Rule #6): a parse failure degrades to the raw utterance; a total
    provider outage degrades to an honest "cannot reach my reasoning engine",
    never to keyword guessing.
  * `unresolved` non-empty means JARVIS ASKS instead of acting. That safety valve
    is the thing a regex never had.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import config as _cfg
from app.services import llm_providers
from app.services.api_key_monitor import get_api_key_monitor

logger = logging.getLogger("J.A.R.V.I.S")

# The five kinds. `mixed` means a real question AND a real action in one message.
KIND_ACTION = "action"
KIND_WEB_QUESTION = "web_question"
KIND_KNOWLEDGE_QUESTION = "knowledge_question"
KIND_VISUAL = "visual"
KIND_MIXED = "mixed"
ALL_KINDS = (KIND_ACTION, KIND_WEB_QUESTION, KIND_KNOWLEDGE_QUESTION,
             KIND_VISUAL, KIND_MIXED)

# Kinds that must reach the agent loop, because only it can act or look.
ACTING_KINDS = (KIND_ACTION, KIND_MIXED, KIND_VISUAL)

SOURCE_LLM = "llm"           # a provider answered and the JSON was usable
SOURCE_FALLBACK = "fallback"  # a provider answered but the JSON was not usable
SOURCE_OFFLINE = "offline"    # no provider answered at all

_MAX_UTTERANCE = 1200
_MAX_TURN_PREVIEW = 400
_MAX_STATE_BLOCK = 1400
_MAX_GOAL = 400

_SYSTEM_PROMPT = """You are the understanding layer of J.A.R.V.I.S, a voice assistant that controls the owner's Windows PC and browser.

You do NOT answer the user and you do NOT perform actions. Your only job is to read the newest user message IN CONTEXT and output what the user actually wants, stated in full.

Output ONLY a JSON object. No prose, no markdown, no code fence.

{
  "goal": "<one self-contained sentence: the request with every reference resolved>",
  "kind": "action" | "web_question" | "knowledge_question" | "visual" | "mixed",
  "self_contained": true | false,
  "refers_to_previous": true | false,
  "is_confirmation": true | false | null,
  "visual_source": "camera" | "screen" | null,
  "unresolved": ["<what you genuinely could not pin down>"],
  "confidence": 0.0
}

=== goal ===
Rewrite the request so someone who just joined the conversation could carry it out with no further context. Resolve every pronoun, every "it/that/this/woh/isko", and every ellipsis from the conversation above.
- If the user is saying a previous attempt did not work, the goal is that ORIGINAL request again, plus what went wrong.
- If the user is adding a detail to an earlier request ("it's a Pakistani song", "by Fahim Abdullah"), the goal is the earlier request enriched with the new detail.
- Carry over the surface the user was already using (a specific site, app or window) when the new message clearly continues it.
- Write the goal in English even when the user speaks Hindi, Hinglish or mixes both.
- Never invent a detail that was not said or clearly implied. If a required detail is missing, leave the goal as close to the request as you can and list the missing detail in "unresolved".

=== kind ===
"action" - the user wants something DONE on this computer or in the browser: open/close an app, play or search something in the browser, type, click, files, volume, brightness, power, Wi-Fi/Bluetooth, email, calendar, drive, reminders, notes, generating an image, writing content.
  ALSO "action" for any question about THIS machine's own state or settings ("is wifi on?", "kitna volume hai", "what's my battery", "what windows are open"). That must be READ from the machine, never answered from the web or from memory.
"web_question" - needs current, live or recent information from the internet: news, weather, prices, scores, who someone is, what happened recently.
"knowledge_question" - answerable from general knowledge, from the conversation, or from what you already know about the owner. Greetings, thanks, small talk, opinions, static facts, arithmetic.
"visual" - the user wants something LOOKED AT: the camera ("what am I holding", "take a selfie") or what is currently on screen ("what options are on this page", "read this window").
"mixed" - one message that clearly contains BOTH a question to answer AND an action to perform.

=== self_contained ===
Would the ORIGINAL message, on its own with no conversation, mean the same thing? "open youtube" -> true. "close it", "play that one", "search for it" -> false.

=== refers_to_previous ===
True when this message depends on, continues, corrects, or complains about an earlier turn.

=== is_confirmation ===
Only meaningful when the context says a confirmation is pending.
  true  = the user is agreeing / telling you to go ahead (yes, haan, kar do, go on, sure, do it)
  false = the user is refusing / cancelling (no, nahi, cancel, rehne do, forget it)
  null  = neither, or nothing is pending. When a confirmation is pending and the user says something unrelated, use null.

=== visual_source ===
Only meaningful when kind is "visual". "camera" when the owner wants the webcam used (holding something up, a selfie, "look at this"). "screen" when they want what is currently displayed on the PC read or inspected. null otherwise.

=== unresolved ===
List anything you genuinely could not determine and that is required to act. Non-empty means J.A.R.V.I.S will ASK the user instead of guessing, so use it when guessing would be wrong -- and leave it EMPTY when you did resolve everything. Do not put stylistic uncertainty here.

=== confidence ===
Your confidence in "goal" and "kind", 0.0 to 1.0."""

_REPAIR_PROMPT = ("Your previous reply was not valid JSON matching the required "
                  "shape. Output ONLY the JSON object, nothing else.")


@dataclass
class Resolution:
    """What the understanding layer made of one turn."""
    goal: str = ""
    kind: str = KIND_KNOWLEDGE_QUESTION
    self_contained: bool = True
    refers_to_previous: bool = False
    is_confirmation: Optional[bool] = None
    # Only meaningful when kind == "visual": which surface has to be looked at.
    # An addition to the plan's contract -- the camera route needs a browser
    # capture while an on-screen question needs the screen-reading tools, and the
    # model is the right place to make that call. The alternative was a keyword
    # list, which is exactly what this milestone removes.
    visual_source: Optional[str] = None
    unresolved: List[str] = field(default_factory=list)
    confidence: float = 0.0
    source: str = SOURCE_OFFLINE
    elapsed_ms: int = 0
    provider: str = ""
    raw: str = ""

    @property
    def ok(self) -> bool:
        """True when a provider answered, even if the JSON needed repairing."""
        return self.source in (SOURCE_LLM, SOURCE_FALLBACK)

    @property
    def understood(self) -> bool:
        """True only when the structured answer is trustworthy."""
        return self.source == SOURCE_LLM

    @property
    def needs_action(self) -> bool:
        return self.kind in ACTING_KINDS

    @property
    def needs_clarification(self) -> bool:
        """Ask instead of guessing -- but only when about to ACT.

        Guessing is dangerous when something will change on the machine: the wrong
        recipient, the wrong file, the wrong window. It is merely unhelpful when
        the turn is a question, and a search engine or a conversation handles a
        vague question far better than an interrogation does ("who won the match
        last night" does not need JARVIS to demand a sport first).
        """
        return bool(self.unresolved) and self.kind in ACTING_KINDS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal": self.goal, "kind": self.kind,
            "self_contained": self.self_contained,
            "refers_to_previous": self.refers_to_previous,
            "is_confirmation": self.is_confirmation,
            "visual_source": self.visual_source,
            "unresolved": list(self.unresolved),
            "confidence": self.confidence,
            "source": self.source, "provider": self.provider,
            "elapsed_ms": self.elapsed_ms,
        }


def _clip(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _extract_json(text: str) -> Optional[dict]:
    """Pull the first JSON object out of a model reply.

    Reasoning models wrap answers in <think> blocks and chat models like to add a
    ```json fence. Neither is an error worth a retry, so both are stripped here.
    """
    raw = str(text or "")
    if "</think>" in raw:
        raw = raw.split("</think>")[-1]
    raw = re.sub(r"<think>.*?</think>", " ", raw, flags=re.DOTALL)
    raw = re.sub(r"^\s*```(?:json)?", "", raw.strip())
    raw = re.sub(r"```\s*$", "", raw.strip())
    raw = raw.strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:  # noqa: BLE001
        pass
    # Fall back to the outermost balanced braces.
    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    for idx in range(start, len(raw)):
        char = raw[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(raw[start:idx + 1])
                    return parsed if isinstance(parsed, dict) else None
                except Exception:  # noqa: BLE001
                    return None
    return None


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "yes", "1"):
            return True
        if low in ("false", "no", "0"):
            return False
    return default


def _coerce_tribool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "yes"):
            return True
        if low in ("false", "no"):
            return False
    return None


class Resolver:
    """Understands one turn. Stateless apart from its LLM clients."""

    def __init__(self, gemini_clients: Optional[list] = None,
                 groq_clients: Optional[list] = None,
                 model: Optional[str] = None,
                 timeout: Optional[int] = None) -> None:
        self.enabled = bool(getattr(_cfg, "RESOLVER_ENABLED", True))
        self.model = model or getattr(_cfg, "RESOLVER_MODEL", "")
        self.timeout = int(timeout if timeout is not None
                           else getattr(_cfg, "RESOLVER_TIMEOUT", 6))
        self.max_history = int(getattr(_cfg, "RESOLVER_MAX_HISTORY_TURNS", 8))
        self.max_failover_keys = max(
            1, int(getattr(_cfg, "RESOLVER_MAX_FAILOVER_KEYS", 3)))
        self.last_provider_event: Optional[Dict[str, Any]] = None
        self._gemini_clients = gemini_clients if gemini_clients is not None else []
        self._groq_clients = groq_clients if groq_clients is not None else []
        self._injected = gemini_clients is not None or groq_clients is not None
        if not self._injected:
            self._build_clients()

    # -- construction ---------------------------------------------------- #
    def _build_clients(self) -> None:
        if llm_providers.gemini_enabled():
            try:
                self._gemini_clients = [
                    llm_providers.make_raw_client(idx, timeout=self.timeout)
                    for idx in range(llm_providers.key_count())
                ]
            except Exception as e:  # noqa: BLE001
                logger.warning("[RESOLVER] Gemini clients unavailable: %s", e)
                self._gemini_clients = []
        try:
            from groq import Groq
            self._groq_clients = [Groq(api_key=key)
                                  for key in getattr(_cfg, "GROQ_API_KEYS", [])]
        except Exception as e:  # noqa: BLE001
            logger.warning("[RESOLVER] Groq clients unavailable: %s", e)
            self._groq_clients = []
        logger.info("[RESOLVER] ready | model=%s | gemini_keys=%d | groq_keys=%d",
                    self.model, len(self._gemini_clients), len(self._groq_clients))

    @property
    def available(self) -> bool:
        return bool(self._gemini_clients or self._groq_clients)

    # -- context --------------------------------------------------------- #
    def build_context(
        self,
        utterance: str,
        chat_history: Optional[List[Tuple[str, str]]] = None,
        state_block: str = "",
        last_action: Optional[Dict[str, Any]] = None,
        memory_facts: str = "",
        confirmation_pending: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Everything the resolver is allowed to look at, in one block."""
        parts: List[str] = []

        turns = list(chat_history or [])[-max(1, self.max_history):]
        if turns:
            lines = []
            for user_text, assistant_text in turns:
                lines.append("User: " + _clip(user_text, _MAX_TURN_PREVIEW))
                lines.append("J.A.R.V.I.S: " + _clip(assistant_text, _MAX_TURN_PREVIEW))
            parts.append("=== Conversation so far ===\n" + "\n".join(lines))
        else:
            parts.append("=== Conversation so far ===\n(nothing yet)")

        if state_block:
            parts.append("=== Live system state ===\n" + _clip(state_block, _MAX_STATE_BLOCK))

        if last_action:
            tool = _clip(last_action.get("tool"), 60)
            args = _clip(json.dumps(last_action.get("args") or {}, default=str), 200)
            verdict = _clip(last_action.get("verdict"), 40) or "not verified"
            reason = _clip(last_action.get("reason"), 160)
            parts.append(
                "=== Last action performed this session ===\n"
                f"tool: {tool}\nargs: {args}\nverification: {verdict}"
                + (f"\nreason: {reason}" if reason else "")
                + "\n(If this did not verify and the user is now complaining, the "
                  "goal is that request again.)"
            )

        if memory_facts:
            parts.append("=== Known facts about the owner ===\n"
                         + _clip(memory_facts, 800))

        if confirmation_pending:
            tool = _clip(confirmation_pending.get("tool"), 60)
            original = _clip(confirmation_pending.get("original_message"), 200)
            parts.append(
                "=== A confirmation is PENDING ===\n"
                f"J.A.R.V.I.S asked the owner to approve running '{tool}' for: "
                f"\"{original}\". Decide is_confirmation for the newest message."
            )
        else:
            parts.append("=== No confirmation is pending ===")

        parts.append("=== Newest user message ===\n" + _clip(utterance, _MAX_UTTERANCE)
                     + "\n\nOutput the JSON object now.")
        return "\n\n".join(parts)

    # -- the call -------------------------------------------------------- #
    def resolve(
        self,
        utterance: str,
        chat_history: Optional[List[Tuple[str, str]]] = None,
        state_block: str = "",
        last_action: Optional[Dict[str, Any]] = None,
        memory_facts: str = "",
        confirmation_pending: Optional[Dict[str, Any]] = None,
    ) -> Resolution:
        """Understand one turn. Never raises."""
        t0 = time.perf_counter()
        text = str(utterance or "").strip()
        if not text:
            return Resolution(goal="", kind=KIND_KNOWLEDGE_QUESTION,
                              source=SOURCE_FALLBACK, elapsed_ms=0)
        if not self.enabled or not self.available:
            return self._offline(text, t0)

        user_content = self.build_context(
            text, chat_history, state_block, last_action, memory_facts,
            confirmation_pending)
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        reply, provider, key_index = self._complete(messages)
        if reply is None:
            logger.warning("[RESOLVER] no provider answered -- degrading honestly")
            return self._offline(text, t0)

        parsed = _extract_json(reply)
        if parsed is None:
            # One re-ask. Models occasionally narrate; asking once is cheaper than
            # guessing, and guessing is what this whole layer exists to remove.
            repair = messages + [{"role": "assistant", "content": reply[:1200]},
                                 {"role": "user", "content": _REPAIR_PROMPT}]
            reply2, provider2, key_index2 = self._complete(repair)
            if reply2 is not None:
                parsed = _extract_json(reply2)
                if parsed is not None:
                    provider, key_index, reply = provider2, key_index2, reply2

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        if parsed is None:
            logger.warning("[RESOLVER] unparseable after re-ask -- using the raw "
                           "utterance as the goal")
            return Resolution(
                goal=text[:_MAX_GOAL], kind=KIND_ACTION, self_contained=True,
                source=SOURCE_FALLBACK, elapsed_ms=elapsed_ms, provider=provider,
                raw=_clip(reply, 300))

        resolution = self._validate(parsed, text)
        resolution.source = SOURCE_LLM
        resolution.elapsed_ms = elapsed_ms
        resolution.provider = provider
        resolution.raw = _clip(reply, 400)
        self._provider_event(provider, key_index)
        logger.info("[RESOLVER] %.60s -> %s | %s | self_contained=%s refs_prev=%s "
                    "unresolved=%s (%dms)",
                    text, resolution.kind, resolution.goal[:80],
                    resolution.self_contained, resolution.refers_to_previous,
                    resolution.unresolved, elapsed_ms)
        return resolution

    def _offline(self, text: str, t0: float) -> Resolution:
        return Resolution(
            goal=text[:_MAX_GOAL], kind=KIND_ACTION, self_contained=True,
            source=SOURCE_OFFLINE,
            elapsed_ms=int((time.perf_counter() - t0) * 1000))

    # -- validation ------------------------------------------------------ #
    @staticmethod
    def _validate(parsed: dict, utterance: str) -> Resolution:
        """Coerce a model's JSON into the contract. Anything odd falls back to a
        safe value rather than propagating a surprise into routing."""
        goal = _clip(parsed.get("goal"), _MAX_GOAL) or utterance[:_MAX_GOAL]
        kind = str(parsed.get("kind") or "").strip().lower()
        if kind not in ALL_KINDS:
            # An unrecognised kind must not silently become "chat", because chat
            # cannot act and is free to promise things. Prefer the agent.
            kind = KIND_ACTION
        unresolved_raw = parsed.get("unresolved")
        if isinstance(unresolved_raw, str):
            unresolved = [unresolved_raw] if unresolved_raw.strip() else []
        elif isinstance(unresolved_raw, list):
            unresolved = [_clip(item, 160) for item in unresolved_raw
                          if str(item or "").strip()]
        else:
            unresolved = []
        try:
            confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.0))))
        except Exception:  # noqa: BLE001
            confidence = 0.0
        visual_source = str(parsed.get("visual_source") or "").strip().lower()
        if visual_source not in ("camera", "screen"):
            visual_source = None
        return Resolution(
            goal=goal,
            kind=kind,
            self_contained=_coerce_bool(parsed.get("self_contained"), True),
            refers_to_previous=_coerce_bool(parsed.get("refers_to_previous"), False),
            is_confirmation=_coerce_tribool(parsed.get("is_confirmation")),
            visual_source=visual_source,
            unresolved=unresolved[:4],
            confidence=confidence,
        )

    # -- providers (fixed order, never raced) ---------------------------- #
    def _complete(self, messages: List[Dict[str, str]]
                  ) -> Tuple[Optional[str], str, int]:
        reply, key_index = self._gemini(messages)
        if reply is not None:
            return reply, "gemini", key_index
        reply, key_index = self._groq(messages)
        if reply is not None:
            return reply, "groq", key_index
        return None, "", 0

    def _gemini(self, messages: List[Dict[str, str]]) -> Tuple[Optional[str], int]:
        if not self._gemini_clients:
            return None, 0
        monitor = get_api_key_monitor()
        # Filter to keys we actually built a client for BEFORE capping, so the cap
        # cannot spend all its attempts on indices that do not exist.
        order = [idx for idx in llm_providers.ordered_keys()
                 if idx < len(self._gemini_clients)]
        if not order:
            order = list(range(len(self._gemini_clients)))
        for idx in order[:self.max_failover_keys]:
            monitor.record_gemini_attempt(idx, operation="resolve", source="resolver")
            t0 = time.perf_counter()
            try:
                completion = self._gemini_clients[idx].chat.completions.create(
                    model=self.model or getattr(_cfg, "GEMINI_BRAIN_MODEL", ""),
                    messages=messages, temperature=0.0, max_tokens=700,
                    timeout=self.timeout,
                    response_format={"type": "json_object"},
                )
                monitor.record_gemini_success(
                    idx, operation="resolve", source="resolver",
                    latency_ms=int((time.perf_counter() - t0) * 1000))
                return (completion.choices[0].message.content or ""), idx
            except Exception as e:  # noqa: BLE001
                monitor.record_gemini_failure(
                    idx, operation="resolve", source="resolver", error=str(e),
                    is_rate_limit=llm_providers.is_rate_limit_error(e),
                    latency_ms=int((time.perf_counter() - t0) * 1000))
                llm_providers.trip(idx)
                logger.warning("[RESOLVER] Gemini key #%d failed: %s",
                               idx + 1, str(e)[:120])
                continue
        return None, 0

    def _groq(self, messages: List[Dict[str, str]]) -> Tuple[Optional[str], int]:
        if not self._groq_clients:
            return None, 0
        monitor = get_api_key_monitor()
        model = getattr(_cfg, "INTENT_CLASSIFY_MODEL", "")
        for idx in range(min(len(self._groq_clients), self.max_failover_keys)):
            monitor.record_groq_attempt(idx, operation="resolve", source="resolver")
            t0 = time.perf_counter()
            try:
                completion = self._groq_clients[idx].chat.completions.create(
                    model=model, messages=messages, temperature=0.0,
                    max_tokens=900, timeout=self.timeout,
                    response_format={"type": "json_object"},
                )
                monitor.record_groq_success(
                    idx, operation="resolve", source="resolver",
                    latency_ms=int((time.perf_counter() - t0) * 1000))
                return (completion.choices[0].message.content or ""), idx
            except Exception as e:  # noqa: BLE001
                monitor.record_groq_failure(
                    idx, operation="resolve", source="resolver", error=str(e),
                    is_rate_limit="429" in str(e),
                    latency_ms=int((time.perf_counter() - t0) * 1000))
                logger.warning("[RESOLVER] Groq key #%d failed: %s",
                               idx + 1, str(e)[:120])
                continue
        return None, 0

    def _provider_event(self, provider: str, key_index: int) -> None:
        if provider == "gemini":
            label = ("GEMINI_API_KEY" if key_index == 0
                     else f"GEMINI_API_KEY_{key_index + 1}")
            pretty = "Gemini"
        elif provider == "groq":
            label = ("GROQ_API_KEY" if key_index == 0
                     else f"GROQ_API_KEY_{key_index + 1}")
            pretty = "Groq"
        else:
            self.last_provider_event = None
            return
        self.last_provider_event = {
            "event": "llm_provider" if provider == "gemini" else "provider_failover",
            "provider": provider, "key_index": key_index, "key_label": label,
            "operation": "resolve", "failover": provider != "gemini",
            "message": f"Understanding \u2192 {pretty} ({label})",
            "route": "resolver",
        }


# --------------------------------------------------------------------------- #
# singleton
# --------------------------------------------------------------------------- #
_resolver: Optional[Resolver] = None
_resolver_lock = threading.Lock()


def get_resolver() -> Resolver:
    global _resolver
    if _resolver is None:
        with _resolver_lock:
            if _resolver is None:
                _resolver = Resolver()
    return _resolver


def reset_resolver() -> None:
    """Drop the singleton (tests + config reloads)."""
    global _resolver
    with _resolver_lock:
        _resolver = None
