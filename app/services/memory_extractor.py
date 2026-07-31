"""Learn durable facts about the user from ordinary conversation.

Why this exists
---------------
`remember` is a tool, so a fact was only ever stored when the agent chose to
call it. It essentially never did. Measured on a real install after 60 recorded
actions::

    memory.db     facts=0   corrections=0
    user_model.db um_facts=0  um_aliases=0

So JARVIS knew what the user had *done* but nothing about who they were. The
regex pass in ``MemoryService.auto_capture`` only catches three explicit
phrasings ("my name is X", "remember that X", "i like X"), which real
conversation rarely uses.

How it works
------------
After the reply has been fully streamed, one cheap LLM call looks at the turn
and returns strict JSON describing anything worth keeping. Because it runs
*after* the response, the user never waits for it.

Design rules
------------
* Off the hot path. Work is queued to a single daemon worker; ``submit()``
  returns immediately and never raises.
* Fail-soft. A bad model reply, a dead key, or malformed JSON must leave the
  chat completely unaffected.
* Reuses existing guards rather than reimplementing them: ``MemoryService``
  rejects secrets (``_looks_secret``) and upserts by ``(category, key)``, so
  duplicates collapse on their own.
* Conservative by construction. The prompt demands durable, user-specific
  facts, and the per-turn cap bounds the damage from an over-eager model.
"""

from __future__ import annotations

import json
import logging
import queue
import re
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger("J.A.R.V.I.S")


def _cfg(name: str, default: Any) -> Any:
    try:
        import config as _config
        return getattr(_config, name, default)
    except Exception:  # noqa: BLE001
        return default


# Categories MemoryService accepts. Anything else is coerced to "general".
_VALID_CATEGORIES = ("user", "preference", "project", "feedback", "general")

_SYSTEM_PROMPT = """You extract durable facts about a user from one turn of conversation.

Return ONLY a JSON object, no prose, no markdown fence:
{"facts": [{"category": "...", "key": "...", "value": "..."}]}

Store a fact ONLY if it will still be true and useful next week.

DO store:
- identity: name, role, city, language, timezone
- stable preferences: "prefers Brave over Chrome", "likes lo-fi while studying"
- ongoing projects, goals, deadlines, recurring commitments
- how they want the assistant to behave: "keep answers short"

Do NOT store:
- one-off commands ("open notepad", "volume 50")
- questions the user asked
- anything the assistant said or did
- transient state ("battery is at 40%", "it is raining")
- passwords, OTPs, API keys, card numbers, or any secret
- anything you are merely guessing at

category must be one of: user, preference, project, feedback, general
key: a short stable slug like "name", "city", "browser". Use "" if none fits.
value: one short sentence, under 200 characters.

If there is nothing durable, return exactly {"facts": []}.
That is the correct answer most of the time."""

_USER_TEMPLATE = """User said:
{user_message}

Assistant replied:
{assistant_reply}

Extract durable facts about the user. JSON only."""


def _first_json_object(text: str) -> Optional[dict]:
    """Pull the first JSON object out of a model reply.

    gpt-oss models are reasoning models: they emit thinking text and sometimes
    a markdown fence around the answer, so the payload is rarely the whole
    string.
    """
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?|```", " ", text)
    start = cleaned.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(cleaned)):
            if cleaned[i] == "{":
                depth += 1
            elif cleaned[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(cleaned[start:i + 1])
                        if isinstance(parsed, dict):
                            return parsed
                    except json.JSONDecodeError:
                        break
        start = cleaned.find("{", start + 1)
    return None


def parse_facts(raw: str, max_facts: int) -> List[Dict[str, str]]:
    """Turn a model reply into a clean, capped list of facts. Never raises."""
    parsed = _first_json_object(raw)
    if not parsed:
        return []
    items = parsed.get("facts")
    if not isinstance(items, list):
        return []

    out: List[Dict[str, str]] = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        value = str(item.get("value") or "").strip()
        if not value or len(value) > 400:
            continue
        category = str(item.get("category") or "general").strip().lower()
        if category not in _VALID_CATEGORIES:
            category = "general"
        key = str(item.get("key") or "").strip()[:60] or None
        signature = (category, key, value.lower())
        if signature in seen:
            continue
        seen.add(signature)
        out.append({"category": category, "key": key, "value": value})
        if len(out) >= max_facts:
            break
    return out


class MemoryExtractor:
    """Background extractor. Construction is cheap; clients are built lazily."""

    def __init__(self, memory=None, llm_factory=None) -> None:
        self.enabled = bool(_cfg("MEMORY_EXTRACT_ENABLED", True))
        self._memory = memory
        self._llm_factory = llm_factory
        self._llms: Optional[list] = None
        self._llm_lock = threading.Lock()
        self._key_index = 0

        self.max_facts = int(_cfg("MEMORY_EXTRACT_MAX_FACTS_PER_TURN", 3))
        self.min_chars = int(_cfg("MEMORY_EXTRACT_MIN_MESSAGE_CHARS", 12))

        # A bounded queue plus one worker: extraction is best-effort, so under
        # load it is correct to drop work rather than pile up threads.
        self._queue: "queue.Queue[tuple]" = queue.Queue(maxsize=32)
        self._worker: Optional[threading.Thread] = None
        self._worker_lock = threading.Lock()
        self.stats = {"queued": 0, "dropped": 0, "extracted": 0, "saved": 0, "errors": 0}

    # -- LLM ------------------------------------------------------------- #
    def _build_llms(self) -> list:
        if self._llm_factory is not None:
            return self._llm_factory()
        try:
            from langchain_groq import ChatGroq
            from config import GROQ_API_KEYS, MEMORY_EXTRACT_MODEL, MEMORY_EXTRACT_TIMEOUT
        except Exception as exc:  # noqa: BLE001
            logger.debug("[MEM-EXTRACT] Groq unavailable: %s", exc)
            return []
        if not GROQ_API_KEYS:
            return []
        return [
            ChatGroq(
                groq_api_key=key,
                model_name=MEMORY_EXTRACT_MODEL,
                temperature=0.0,
                # A reasoning model spends tokens thinking before the answer,
                # so a tight cap truncates the JSON. Same lesson as BrainService.
                max_tokens=512,
                request_timeout=MEMORY_EXTRACT_TIMEOUT,
                max_retries=0,
            )
            for key in GROQ_API_KEYS
        ]

    def _llm_pool(self) -> list:
        if self._llms is None:
            with self._llm_lock:
                if self._llms is None:
                    self._llms = self._build_llms()
        return self._llms

    def _invoke(self, user_message: str, assistant_reply: str) -> str:
        pool = self._llm_pool()
        if not pool:
            return ""
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
        except Exception:  # noqa: BLE001
            return ""

        content = _USER_TEMPLATE.format(
            user_message=user_message[:1500],
            assistant_reply=(assistant_reply or "")[:800],
        )
        # Rotate keys so extraction spreads across the pool like every other
        # Groq caller, instead of hammering key 0.
        start = self._key_index
        self._key_index = (self._key_index + 1) % len(pool)
        for offset in range(len(pool)):
            llm = pool[(start + offset) % len(pool)]
            try:
                response = llm.invoke([
                    SystemMessage(content=_SYSTEM_PROMPT),
                    HumanMessage(content=content),
                ])
                return str(getattr(response, "content", "") or "")
            except Exception as exc:  # noqa: BLE001 - try the next key
                logger.debug("[MEM-EXTRACT] key %d failed: %s",
                             (start + offset) % len(pool), exc)
        return ""

    # -- persistence ------------------------------------------------------ #
    def _mem(self):
        if self._memory is not None:
            return self._memory
        from app.services.memory_service import get_memory
        return get_memory()

    def _save(self, facts: List[Dict[str, str]]) -> int:
        memory = self._mem()
        saved = 0
        for fact in facts:
            try:
                # remember() redacts secrets and upserts, so duplicates collapse.
                reply = memory.remember(fact["value"], category=fact["category"],
                                        key=fact["key"], source="auto-llm")
                if isinstance(reply, str) and reply.lower().startswith("got it"):
                    saved += 1
            except Exception as exc:  # noqa: BLE001
                logger.debug("[MEM-EXTRACT] save failed: %s", exc)

        # Mirror user-scoped facts into Phase 8 so personalization has material
        # to inject; um_facts was empty for the same reason facts was.
        for fact in facts:
            if fact["category"] not in ("user", "preference") or not fact["key"]:
                continue
            try:
                from app.services.agent.personalization import get_phase8
                get_phase8().set_fact(fact["key"], fact["value"], source="auto-llm")
            except Exception as exc:  # noqa: BLE001 - Phase 8 is optional
                logger.debug("[MEM-EXTRACT] phase8 mirror skipped: %s", exc)
        return saved

    # -- public API ------------------------------------------------------- #
    def should_consider(self, user_message: str) -> bool:
        if not self.enabled:
            return False
        message = (user_message or "").strip()
        return len(message) >= self.min_chars

    def extract_now(self, user_message: str, assistant_reply: str = "") -> List[Dict[str, str]]:
        """Synchronous extraction. Used by the worker and by tests."""
        raw = self._invoke(user_message, assistant_reply)
        return parse_facts(raw, self.max_facts)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                user_message, assistant_reply = item
                facts = self.extract_now(user_message, assistant_reply)
                if facts:
                    self.stats["extracted"] += len(facts)
                    self.stats["saved"] += self._save(facts)
                    logger.info("[MEM-EXTRACT] learned %d fact(s): %s", len(facts),
                                "; ".join(f["value"][:60] for f in facts))
            except Exception as exc:  # noqa: BLE001 - the worker must never die
                self.stats["errors"] += 1
                logger.debug("[MEM-EXTRACT] worker error: %s", exc)
            finally:
                self._queue.task_done()

    def _ensure_worker(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        with self._worker_lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(target=self._run, name="memory-extractor",
                                            daemon=True)
            self._worker.start()

    def submit(self, user_message: str, assistant_reply: str = "") -> bool:
        """Queue a turn for extraction. Returns immediately; never raises."""
        try:
            if not self.should_consider(user_message):
                return False
            self._ensure_worker()
            self._queue.put_nowait((user_message, assistant_reply))
            self.stats["queued"] += 1
            return True
        except queue.Full:
            self.stats["dropped"] += 1
            logger.debug("[MEM-EXTRACT] queue full, dropping turn")
            return False
        except Exception as exc:  # noqa: BLE001
            logger.debug("[MEM-EXTRACT] submit failed: %s", exc)
            return False

    def drain(self, timeout: float = 10.0) -> None:
        """Block until queued work finishes. Tests and shutdown only."""
        try:
            deadline = threading.Event()
            waiter = threading.Thread(target=lambda: (self._queue.join(), deadline.set()),
                                      daemon=True)
            waiter.start()
            deadline.wait(timeout)
        except Exception:  # noqa: BLE001
            pass


_singleton: Optional[MemoryExtractor] = None
_singleton_lock = threading.Lock()


def get_memory_extractor() -> MemoryExtractor:
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = MemoryExtractor()
    return _singleton
