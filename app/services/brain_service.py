"""
Brain service — Stage-1 category classification only.

This decides the high-level route for a user message:
    general | realtime | camera | task | mixed

It no longer extracts specific task types or payloads. All task execution is
now handled by the agentic tool-calling loop (see app/services/agent/), where
the LLM itself decides which tools to call. This removes the old brittle,
hardcoded regex/keyword task parsing.

  - task    : the user wants an action performed (handled by the agent loop)
  - mixed   : the user wants an action AND a conversational answer (agent loop)
  - camera  : the user wants the assistant to see something (vision)
  - realtime: needs current/live info (web search)
  - general : chat from knowledge only
"""

import logging
import re
import time
from typing import List, Optional, Tuple, Literal

from config import (
    GROQ_API_KEYS,
    INTENT_CLASSIFY_MODEL,
    BRAIN_CLASSIFY_TIMEOUT,
    GEMINI_BRAIN_MODEL,
    GEMINI_CLASSIFY_TIMEOUT,
)
from app.services import llm_providers

logger = logging.getLogger("J.A.R.V.I.S")

CategoryType = Literal["general", "realtime", "camera", "task", "mixed"]
ALL_CATEGORIES: List[str] = ["general", "realtime", "camera", "task", "mixed"]

MAX_CONTEXT_TURNS = 6
MAX_MESSAGE_PREVIEW = 600

_PRIMARY_BRAIN_PROMPT = """You are the decision-maker for JARVIS. Classify the user's message into EXACTLY ONE category.

=== CATEGORIES ===

**camera** — User wants to ANALYZE, IDENTIFY, SEE, or CAPTURE something visual through the camera.
Examples: "What is this?" / "What am I holding?" / "What do you see?" / "Identify this" / "Look at this" / "Read this" / "Take a photo" / "Photo lo" / "Take a selfie" / "Camera on karke dekho" / "Capture this"
- Capturing a photo/selfie with the camera is ALSO camera (the app opens the camera and captures). NOTE: "generate/draw/create an image of X" is NOT camera -> that is task (image generation).

**task** — User wants an ACTION performed on their computer or browser. This includes:
opening/closing apps, typing, clicking, taking screenshots, controlling volume/brightness, media control, system power, file operations, opening websites, playing music/video, searching Google/YouTube, generating images, writing content, checking Gmail, managing Google Calendar, and using Google Drive.
Examples: "Open notepad" / "Type hello and save it" / "Set volume to 50" / "Take a screenshot" / "Open YouTube and play despacito" / "Generate an image of a cat" / "Write an essay about AI" / "Check my inbox" / "What are my events today" / "Shut down the PC" / "Increase brightness" / "Open chrome, go to gmail, click compose"
- ANY request to do/perform/control something on the computer -> task
- Multi-step automation (open X, then do Y, then click Z) -> task
- Requests phrased as questions but asking to check inbox/calendar/drive -> task
- PRINCIPLE: ANY question about THIS computer's OWN state or settings -> task. It must be READ from this machine via system tools, NEVER answered from the web. This covers Wi-Fi, Bluetooth, volume, mute, brightness, battery, and which apps/windows are open. Examples: "is wifi on?" / "wifi on hai kya" / "is bluetooth on?" / "kitna volume hai" / "is it muted" / "what windows are open". Device/settings state = local = task.

**mixed** — User's message contains BOTH a conversational question AND an action in the SAME message.
Examples: "What is machine learning? Also generate an image of a neural network" / "Tell me about Python and open the docs website"
- ONLY use mixed when there is a clear question AND a clear action together.
- "search the internet" / "look it up" alone means they want info -> realtime, NOT mixed.

**realtime** — User needs CURRENT, LIVE, or RECENT information that requires web search.
Examples: "Who is Elon Musk?" / "Latest news" / "What's the weather?" / "Current stock price" / "Tell me about [person/event]"
- Questions about people, events, prices, reviews, news, anything time-sensitive -> realtime
- When unsure if knowledge is current enough -> realtime
- BUT NOT the user's OWN device state/settings (Wi-Fi, Bluetooth, volume, brightness, what's open). Those are LOCAL -> task, never realtime/web.

**general** — Chat from knowledge only. No web search, no action needed.
Examples: "Hello" / "Tell me a joke" / "What is 2+2?" / "Capital of France?" / "How do I improve my coding?" / "Thanks"
- Greetings, casual chat, opinions, advice, static facts, personal stored data -> general

=== CONTEXT ===
Read the conversation history. For corrections/clarifications ("no I meant...", "try again"),
classify as the SAME category as the original request being corrected.

=== RULES ===
- Output EXACTLY ONE word: general, realtime, camera, task, or mixed
- Nothing else. No explanation.
- Any computer/browser action -> task
- Question + action together -> mixed
- Any question about the user's OWN device/system state or settings (wifi, bluetooth, volume, brightness, battery, what's open) -> task (read locally, NEVER web)
- When in doubt between general and realtime -> realtime
- When in doubt whether an action is requested -> task"""


# Cap Gemini failover attempts in the routing path. Routing has an instant
# rule-based safety net, so when Gemini is provider-wide degraded (503 "high
# demand") we fast-fail after a few keys instead of cycling every key at ~4s
# each -- the cause of the 14-22s classify times seen in the logs.
_CLASSIFY_FAILOVER_KEYS = 3


class BrainService:
    def __init__(self, groq_service=None):
        self.groq_service = groq_service
        self._llms = []

        if GROQ_API_KEYS:
            try:
                from langchain_groq import ChatGroq
                self._llms = [
                    ChatGroq(
                        groq_api_key=key,
                        model_name=INTENT_CLASSIFY_MODEL,
                        temperature=0.0,
                        # gpt-oss is a REASONING model: it spends tokens "thinking"
                        # before emitting the final word. 20 tokens was far too few,
                        # so the answer got truncated and parsing fell back to
                        # "general" (every action was misrouted). Give it room.
                        max_tokens=512,
                        request_timeout=BRAIN_CLASSIFY_TIMEOUT,
                        max_retries=0,
                    )
                    for key in GROQ_API_KEYS
                ]
                logger.info("[BRAIN] Category classifier initialized (%s) with %d key(s)",
                            INTENT_CLASSIFY_MODEL, len(self._llms))
            except Exception as e:
                logger.warning("[BRAIN] Failed to create Groq: %s", e)

        if not self._llms:
            logger.warning("[BRAIN] No Groq LLM. Will use rule-based fallback.")

        # Optional Gemini classifier (secondary provider). Used for failover and,
        # when race mode is on, fired in parallel with Groq (first answer wins).
        self.last_provider_event = None
        self._gemini_llms = []
        if llm_providers.gemini_enabled():
            try:
                self._gemini_llms = [
                    llm_providers.make_langchain_llm(
                        idx,
                        model=GEMINI_BRAIN_MODEL,
                        temperature=0.0,
                        max_tokens=512,
                        timeout=GEMINI_CLASSIFY_TIMEOUT,
                    )
                    for idx in range(llm_providers.key_count())
                ]
                logger.info(
                    "[BRAIN] Gemini classifier ready with %d key(s) (failover only; routing is not raced)",
                    len(self._gemini_llms),
                )
            except Exception as e:
                logger.warning("[BRAIN] Failed to create Gemini classifier: %s", e)
                self._gemini_llms = []

    def classify_primary(
        self,
        user_message: str,
        chat_history: Optional[List[Tuple[str, str]]] = None,
        key_index: int = 0,
    ) -> Tuple[str, str, int]:
        msg = (user_message or "").strip()
        if not msg:
            return ("general", "empty", 0)

        user_content = self._build_context(msg, chat_history)
        t0 = time.perf_counter()
        self.last_provider_event = None
        # Routing is a precise decision (not fungible text), so we deliberately do
        # NOT race two different models here -- racing made the route inconsistent
        # (e.g. "latest news" sometimes landed on 'general'). Groq decides for
        # consistency, with Gemini as failover only (handled inside _run_llm).
        category, method = self._run_llm(user_content, key_index)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        logger.info("[BRAIN-PRIMARY] %s -> %s (%d ms, %s)", msg[:50], category, elapsed_ms, method)
        return (category, method, elapsed_ms)

    def _build_context(self, msg: str, chat_history: Optional[List[Tuple[str, str]]] = None) -> str:
        context_lines = []
        if chat_history:
            for u, a in chat_history[-MAX_CONTEXT_TURNS:]:
                u_preview = (u or "")[:MAX_MESSAGE_PREVIEW]
                a_preview = (a or "")[:MAX_MESSAGE_PREVIEW]
                context_lines.append(f"User: {u_preview}")
                context_lines.append(f"Assistant: {a_preview}")
        context_block = "\n".join(context_lines) if context_lines else "(No prior conversation)"
        msg_preview = msg[:MAX_MESSAGE_PREVIEW]
        return f"""Conversation so far:
{context_block}

Current user message: {msg_preview}

Classify. Output EXACTLY ONE category name."""

    def _run_llm(self, user_content: str, key_index: int) -> Tuple[str, str]:
        # Primary: Groq (fast, primary-first key). On failure fall over to Gemini
        # (if enabled), then finally the rule-based classifier.
        if self._llms:
            try:
                category, idx = self._groq_once(user_content, key_index)
                self._provider_event("groq", idx, failover=False)
                return (category, "llm")
            except Exception as e:
                logger.warning("[BRAIN] Groq classify failed: %s", e)

        if self._gemini_llms:
            try:
                category, idx = self._gemini_attempt(
                    user_content, single=False, max_keys=_CLASSIFY_FAILOVER_KEYS)
                self._provider_event("gemini", idx, failover=True)
                logger.info("[BRAIN] Gemini fallback classify succeeded (key #%d)", idx + 1)
                return (category, "llm")
            except Exception as e:
                logger.warning("[BRAIN] Gemini classify failed: %s", e)

        msg = user_content.split("Current user message:")[-1].strip()[:500] if "Current user message:" in user_content else user_content[:500]
        return (self._rule_based_primary(msg), "rule-based")

    def _provider_event(self, provider: str, key_index: int, failover: bool = False, race: bool = False):
        """Record which provider/key answered the brain classification."""
        if provider == "gemini":
            label = "GEMINI_API_KEY" if key_index == 0 else f"GEMINI_API_KEY_{key_index + 1}"
            pretty = "Gemini"
        else:
            label = "GROQ_API_KEY" if key_index == 0 else f"GROQ_API_KEY_{key_index + 1}"
            pretty = "Groq"
        if race:
            message = f"Brain race \u2192 {pretty} ({label}) won"
            event = "provider_race"
        elif failover:
            message = f"Brain \u2192 {pretty} ({label}) (failover)"
            event = "provider_failover"
        else:
            message = f"Brain \u2192 {pretty} ({label})"
            event = "llm_provider"
        self.last_provider_event = {
            "event": event,
            "provider": provider,
            "key_index": key_index,
            "key_label": label,
            "operation": "brain_classify",
            "failover": bool(failover),
            "race": bool(race),
            "message": message,
            "route": "brain",
        }
        return self.last_provider_event

    def _groq_once(self, user_content: str, key_index: int) -> Tuple[str, int]:
        """Single Groq classification attempt on one key. Raises on failure."""
        from langchain_core.messages import SystemMessage, HumanMessage
        from app.services.api_key_monitor import get_api_key_monitor
        idx = key_index % len(self._llms)
        monitor = get_api_key_monitor()
        monitor.record_groq_attempt(idx, operation="brain_primary", source="brain_service")
        t0_llm = time.perf_counter()
        try:
            response = self._llms[idx].invoke([
                SystemMessage(content=_PRIMARY_BRAIN_PROMPT),
                HumanMessage(content=user_content),
            ])
            latency_ms = int((time.perf_counter() - t0_llm) * 1000)
            monitor.record_groq_success(idx, operation="brain_primary", source="brain_service", latency_ms=latency_ms)
            return (self._parse_single((response.content or "").strip().lower()), idx)
        except Exception as e:
            latency_ms = int((time.perf_counter() - t0_llm) * 1000)
            is_rl = "429" in str(e) or "rate limit" in str(e).lower()
            monitor.record_groq_failure(idx, operation="brain_primary", source="brain_service", error=str(e), is_rate_limit=is_rl, latency_ms=latency_ms)
            raise

    def _gemini_attempt(self, user_content: str, single: bool = False,
                        max_keys: Optional[int] = None) -> Tuple[str, int]:
        """Gemini classification. single=True tries just one key (for racing).

        max_keys caps how many keys we cycle before giving up. The routing
        failover path passes a small cap so a provider-wide outage (Gemini 503
        'high demand') fast-fails to the instant rule-based classifier instead
        of spending ~4s per key across every key -- the cause of the 14-22s
        classify times seen in the logs."""
        from langchain_core.messages import SystemMessage, HumanMessage
        from app.services.api_key_monitor import get_api_key_monitor
        monitor = get_api_key_monitor()
        order = llm_providers.ordered_keys()
        if single and order:
            order = order[:1]
        elif max_keys is not None and order:
            order = order[:max_keys]
        last_exc = None
        for idx in order:
            if idx >= len(self._gemini_llms):
                continue
            monitor.record_gemini_attempt(idx, operation="brain_primary", source="brain_service")
            t0 = time.perf_counter()
            try:
                response = self._gemini_llms[idx].invoke([
                    SystemMessage(content=_PRIMARY_BRAIN_PROMPT),
                    HumanMessage(content=user_content),
                ])
                latency_ms = int((time.perf_counter() - t0) * 1000)
                monitor.record_gemini_success(idx, operation="brain_primary", source="brain_service", latency_ms=latency_ms)
                return (self._parse_single((response.content or "").strip().lower()), idx)
            except Exception as e:
                last_exc = e
                latency_ms = int((time.perf_counter() - t0) * 1000)
                rl = llm_providers.is_rate_limit_error(e)
                monitor.record_gemini_failure(idx, operation="brain_primary", source="brain_service", error=str(e), is_rate_limit=rl, latency_ms=latency_ms)
                llm_providers.trip(idx)
                continue
        raise last_exc if last_exc is not None else RuntimeError("No Gemini keys available")

    def _race_classify(self, user_content: str, key_index: int) -> Tuple[str, str]:
        """Fire Groq and Gemini together; take whichever answers first (the 'cool'
        race the user liked). Side-effect-free, so racing is safe here."""
        import concurrent.futures

        def groq_task():
            return ("groq",) + self._groq_once(user_content, key_index)

        def gem_task():
            cat, idx = self._gemini_attempt(user_content, single=True)
            return ("gemini", cat, idx)

        winner = None
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        try:
            futs = [ex.submit(groq_task), ex.submit(gem_task)]
            for fut in concurrent.futures.as_completed(futs):
                try:
                    provider, cat, idx = fut.result()
                    winner = (provider, cat, idx)
                    break
                except Exception as e:
                    logger.warning("[BRAIN-RACE] provider failed: %s", e)
                    continue
        finally:
            # Don't block on the loser; the leftover request finishes in the background.
            ex.shutdown(wait=False)

        if winner:
            provider, cat, idx = winner
            self._provider_event(provider, idx, race=True)
            logger.info("[BRAIN-RACE] %s won the race -> %s", provider, cat)
            return (cat, "llm")

        msg = user_content.split("Current user message:")[-1].strip()[:500] if "Current user message:" in user_content else user_content[:500]
        return (self._rule_based_primary(msg), "rule-based")

    def _parse_single(self, text: str) -> str:
        if not text:
            return "general"
        text = text.strip().lower()
        # Reasoning models (gpt-oss, etc.) may wrap their chain-of-thought in
        # <think>...</think> or emit a verbose explanation before the verdict.
        # Focus on the CONCLUSION: drop everything up to the last </think>, then
        # strip any remaining think blocks.
        if "</think>" in text:
            text = text.split("</think>")[-1]
        text = re.sub(r"<think>.*?</think>", " ", text, flags=re.DOTALL)
        cleaned = text.strip().strip(".:!?\"'`* \n\t")
        # 1) Clean single-word answer (the common case once the model has room).
        if cleaned in ALL_CATEGORIES:
            return cleaned
        # 2) Otherwise scan for category words (whole-word match so "general"
        #    inside other text doesn't win) and prefer the one mentioned LAST —
        #    a reasoning model states its final verdict at the end.
        best_cat, best_pos = None, -1
        for opt in ALL_CATEGORIES:
            for m in re.finditer(r"\b" + re.escape(opt) + r"\b", text):
                if m.start() > best_pos:
                    best_pos, best_cat = m.start(), opt
        if best_cat:
            return best_cat
        return "general"

    def _rule_based_primary(self, msg: str) -> str:
        """Lightweight fallback used only if the classifier LLM is unavailable.
        Deliberately simple — the agent loop is robust to the route anyway."""
        m = (msg or "").strip().lower()

        if m in ("hello", "hi", "hey", "good morning", "good evening", "good afternoon",
                 "how are you", "what's up", "thanks", "thank you", "bye", "goodbye"):
            return "general"

        if any(x in m for x in ["what do you see", "what can you see", "what am i holding",
                                "what is this", "describe this", "identify this",
                                "what's in my hand", "look at this", "read this",
                                "can you see", "check this out",
                                "photo lo", "photo le", "take a selfie", "selfie",
                                "capture this", "camera on", "open camera",
                                "camera se", "meri photo", "click my photo"]):
            return "camera"

        action_signals = [
            "open ", "close ", "launch ", "start ", "play ", "type ", "click ",
            "screenshot", "volume", "brightness", "mute", "shut down", "shutdown",
            "restart", "reboot", "sleep", "lock ", "write ", "draft ", "generate ",
            "draw ", "create image", "picture of", "image of", "search ", "google ",
            "youtube", "inbox", "unread", "email", "mail", "calendar", "event",
            "drive", "delete ", "save ", "set ", "increase ", "decrease ", "press ",
            "go to ", "visit ", "media", "next track", "pause", "minimize", "maximize",
        ]
        if any(x in m for x in action_signals):
            return "task"

        if any(x in m for x in ["who is ", "who are ", "latest", "current", "news", "weather",
                                "today", "recent", "stock price", "trending", "score",
                                "tell me about ", "what happened", "how much does",
                                "price of", "cost of", "reviews of", "best restaurants"]):
            return "realtime"

        return "general"
