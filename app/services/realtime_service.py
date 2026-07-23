from typing import List, Optional, Iterator, Tuple, Any
import requests as http_requests
import logging
import os
import re
import time
from pathlib import Path
from app.services.groq_service import GroqService, escape_curly_braces, AllGroqApisFailedError
from app.services.vector_store import VectorStoreService
from app.services.api_key_monitor import get_api_key_monitor
from app.services import llm_providers
from app.utils.retry import with_retry
from config import REALTIME_CHAT_ADDENDUM, GROQ_API_KEYS, GROQ_MODEL, INTENT_CLASSIFY_MODEL, SERPER_API_KEYS, SERPER_API_KEY, GEMINI_BRAIN_MODEL, GEMINI_CLASSIFY_TIMEOUT

_AMBIGUOUS_REF_RE = re.compile(r"\b(it|that|this|those|these|him|her|them|there|here)\b", re.IGNORECASE)
_EXPLICIT_LOCATION_RE = re.compile(r"\b(in|at|for)\s+([A-Za-z][A-Za-z\s\-]{1,60})\b", re.IGNORECASE)

logger = logging.getLogger("J.A.R.V.I.S")
GROQ_REQUEST_TIMEOUT_FAST = 15
SERPER_API_URL = "https://google.serper.dev/search"
SERPER_REQUEST_TIMEOUT = 10

_NO_RESULTS_WARNING = (
    "\n\nCRITICAL: The web search returned NO results for this query. "
    "Do NOT answer from your training data — your knowledge is outdated and will give wrong prices/rates/facts. "
    "Instead, say something like: 'I couldn't find the latest data for this right now. Please try asking again or rephrase your question.' "
    "NEVER fabricate numbers, prices, exchange rates, or statistics."
)

_QUERY_EXTRACTION_PROMPT = (
    "You are a search query optimizer. Convert the user's message into a clean, focused "
    "web search query (max 10 words). Rules:\n"
    "- Remove filler words (you know, like, something, can you, tell me, search)\n"
    "- Add specifics: dates (today, 2026), event names, full names\n"
    "- For sports: include league name, team names, 'live score today'\n"
    "- For people: include full name + what user wants to know\n"
    "- For prices/rates: include 'current', 'today', the exact item/currency\n"
    "- Resolve references (him, that, it, no not that) from conversation history\n"
    "- For corrections like 'No, I meant X' or 'Not that, X': understand what the user ACTUALLY wants and create a NEW complete search query\n"
    "Output ONLY the search query. Nothing else."
)

class RealtimeGroqService(GroqService):

    def __init__(self, vector_store_service: VectorStoreService):
        super().__init__(vector_store_service)
        self._user_location = self._load_user_location()

        self.serper_api_keys = list(SERPER_API_KEYS) if SERPER_API_KEYS else []
        if self.serper_api_keys:
            logger.info("Serper (Google Search) initialized with %d API key(s)", len(self.serper_api_keys))
        else:
            logger.warning("SERPER_API_KEY not set. Realtime search will be unavailable.")

        if GROQ_API_KEYS:
            from langchain_groq import ChatGroq
            self._fast_llm = ChatGroq(
                groq_api_key=GROQ_API_KEYS[0],
                model_name=INTENT_CLASSIFY_MODEL,
                temperature=0.0,
                request_timeout=GROQ_REQUEST_TIMEOUT_FAST,
                max_tokens=50,
            )
        else:
            self._fast_llm = None

        # Gemini fast-extract provider (secondary) for query-extraction failover/race
        self._gemini_fast_llms = []
        if llm_providers.gemini_enabled():
            try:
                self._gemini_fast_llms = [
                    llm_providers.make_langchain_llm(
                        idx, model=GEMINI_BRAIN_MODEL, temperature=0.0,
                        max_tokens=50, timeout=GEMINI_CLASSIFY_TIMEOUT,
                    )
                    for idx in range(llm_providers.key_count())
                ]
                logger.info(
                    "[GEMINI] Realtime fast-extract ready: %d key(s)%s",
                    len(self._gemini_fast_llms),
                    " (RACE mode ON)" if llm_providers.race_enabled() else "",
                )
            except Exception as e:
                logger.warning("[GEMINI] Realtime fast-extract init failed: %s", e)
                self._gemini_fast_llms = []

    def _load_user_location(self) -> str:
        """Load user location from env var or auto-detect from learning_data files."""
        env_location = (os.getenv("JARVIS_USER_LOCATION", "") or "").strip()
        if env_location:
            logger.info("[REALTIME] Using user location from env: %s", env_location)
            return env_location

        try:
            learning_dir = Path(__file__).resolve().parents[2] / "database" / "learning_data"
            if not learning_dir.exists():
                return ""
            patterns = [
                re.compile(r"\blocation\s*:\s*([A-Za-z][A-Za-z\s\-,]{1,60})", re.IGNORECASE),
                re.compile(r"\blive in\s+([A-Za-z][A-Za-z\s\-]{1,60})", re.IGNORECASE),
                re.compile(r"\bcity\s*:\s*([A-Za-z][A-Za-z\s\-]{1,60})", re.IGNORECASE),
            ]
            for txt_file in sorted(learning_dir.glob("*.txt")):
                try:
                    text = txt_file.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                for pattern in patterns:
                    match = pattern.search(text)
                    if not match:
                        continue
                    # Take first part before comma for clean city name
                    raw = match.group(1).strip(" ,.;:-")
                    location = raw.split(",")[0].strip()
                    if len(location) >= 2:
                        logger.info("[REALTIME] Inferred user location from learning data: %s", location)
                        return location
        except Exception as e:
            logger.warning("[REALTIME] Failed to infer user location: %s", e)

        return ""

    @staticmethod
    def _is_weather_query(query: str) -> bool:
        """Return True if the query is about weather/temperature/forecast."""
        q = (query or "").lower()
        weather_terms = (
            "weather", "temperature", "forecast", "rain", "humidity",
            "wind", "sunrise", "sunset", "feels like", "climate",
            "mausam", "barish", "garmi", "sardi",
        )
        return any(term in q for term in weather_terms)

    def _maybe_ground_location(self, query: str) -> str:
        """Append user's city to weather queries that don't have an explicit location."""
        q = (query or "").strip()
        if not q or not self._user_location:
            return q
        if not self._is_weather_query(q):
            return q

        q_lower = q.lower()
        loc_lower = self._user_location.lower().split(",")[0].strip().lower()
        if loc_lower in q_lower:
            return q
        if _EXPLICIT_LOCATION_RE.search(q):
            return q

        grounded = f"{q}, {self._user_location}"
        logger.info("[REALTIME] Location-grounded query: '%s' -> '%s'", q[:80], grounded[:120])
        return grounded

    def _safe_fallback_query(self, question: str) -> str:
        """Sanitize a raw question before using it as a literal search query.

        The realtime path falls back to the raw user question when LLM query
        extraction fails. For a normal short question that's fine, but an
        INTERNAL multi-line prompt (e.g. the daily startup brief, which embeds
        the user's unread-email count and calendar status) must never reach the
        web search provider verbatim. Keep only the first meaningful line and
        cap the length so private context can't leak on extraction failure.
        """
        q = (question or "").strip()
        if not q:
            return q
        lines = [ln.strip() for ln in q.splitlines() if ln.strip()]
        first_line = lines[0] if lines else q
        # Multi-line or very long -> almost certainly an internal instruction
        # prompt; keep just the first line. Otherwise use the question as-is.
        safe = first_line if (len(lines) > 1 or len(q) > 160) else q
        if len(safe) > 160:
            safe = safe[:160].rsplit(" ", 1)[0]
        return safe

    def _extract_search_query(
        self, question: str, chat_history: Optional[List[tuple]] = None
    ) -> str:

        self.last_provider_event = None
        if not self._fast_llm and not self._gemini_fast_llms:
            return self._maybe_ground_location(self._safe_fallback_query(question))

        q = question.strip()
        q_lower = q.lower()

        has_filler = any(p in q_lower for p in (
            " it ", " that ", " him ", " her ", " them ", " you know ",
            " something ", " like ", " going on ", " can you ", " tell me ",
            " search ", " right now", " please",
        ))

        has_context = bool(chat_history and len(chat_history) > 0)

        # Short, unambiguous queries without context: skip LLM but still ground location
        if len(q) <= 60 and not has_filler and not has_context and not _AMBIGUOUS_REF_RE.search(q):
            return self._maybe_ground_location(q)

        try:

            t0 = time.perf_counter()

            history_context = ""

            if chat_history:
                recent = chat_history[-3:]
                parts = []

                for h, a in recent:
                    parts.append(f"User: {h[:200]}")
                    parts.append(f"Assistant: {a[:200]}")
                history_context = "\n".join(parts)

            if history_context:
                full_prompt = (
                    f"{_QUERY_EXTRACTION_PROMPT}\n\n"
                    f"Recent conversation:\n{history_context}\n\n"
                    f"User's latest message: {question}\n\n"
                    f"Search query:"
                )

            else:
                full_prompt = (
                    f"{_QUERY_EXTRACTION_PROMPT}\n\n"
                    f"User's message: {question}\n\n"
                    f"Search query:"
                )

            extracted = self._extract_llm_text(full_prompt)
            if not extracted:
                logger.warning("[REALTIME] Query extraction: all providers failed, using sanitized raw question")
                return self._maybe_ground_location(self._safe_fallback_query(question))
            extracted = extracted.strip().strip('"').strip("'")

            if extracted and 3 <= len(extracted) <= 200:
                grounded = self._maybe_ground_location(extracted)
                logger.info(
                    "[REALTIME] Query extraction: '%s' -> '%s' (%.3fs)",
                    question[:80], grounded[:80], time.perf_counter() - t0,
                )
                return grounded

            logger.warning("[REALTIME] Query extraction returned unusable result, using sanitized raw question")
            return self._maybe_ground_location(self._safe_fallback_query(question))

        except Exception as e:
            get_api_key_monitor().record_groq_failure(
                0, operation="realtime_query_extract", source="realtime_service", error=str(e),
                is_rate_limit=("429" in str(e) or "rate limit" in str(e).lower())
            )
            logger.warning("[REALTIME] Query extraction failed (%s), using sanitized raw question", e)
            return self._maybe_ground_location(self._safe_fallback_query(question))

    def _extract_llm_text(self, prompt: str) -> Optional[str]:
        """Query extraction via Groq (primary) with Gemini failover; race when enabled."""
        if llm_providers.race_enabled() and self._fast_llm and self._gemini_fast_llms:
            return self._race_extract(prompt)
        if self._fast_llm:
            text = self._groq_extract(prompt)
            if text:
                self._set_extract_provider("groq", 0, failover=False)
                return text
        if self._gemini_fast_llms:
            return self._gemini_extract(prompt, failover=bool(self._fast_llm))
        return None

    def _groq_extract(self, prompt: str) -> Optional[str]:
        monitor = get_api_key_monitor()
        t0 = time.perf_counter()
        monitor.record_groq_attempt(0, operation="realtime_query_extract", source="realtime_service")
        try:
            response = self._fast_llm.invoke(prompt)
            text = (response.content or "").strip()
            monitor.record_groq_success(0, operation="realtime_query_extract", source="realtime_service", latency_ms=int((time.perf_counter() - t0) * 1000))
            return text or None
        except Exception as e:
            monitor.record_groq_failure(0, operation="realtime_query_extract", source="realtime_service", error=str(e), is_rate_limit=("429" in str(e) or "rate limit" in str(e).lower()), latency_ms=int((time.perf_counter() - t0) * 1000))
            logger.warning("[REALTIME] Groq query extraction failed: %s", e)
            return None

    def _gemini_extract(self, prompt: str, failover: bool = True) -> Optional[str]:
        monitor = get_api_key_monitor()
        for idx in llm_providers.ordered_keys():
            if idx >= len(self._gemini_fast_llms):
                continue
            t0 = time.perf_counter()
            monitor.record_gemini_attempt(idx, operation="realtime_query_extract", source="realtime_service")
            try:
                response = self._gemini_fast_llms[idx].invoke(prompt)
                text = (response.content or "").strip()
                if text:
                    monitor.record_gemini_success(idx, operation="realtime_query_extract", source="realtime_service", latency_ms=int((time.perf_counter() - t0) * 1000))
                    self._set_extract_provider("gemini", idx, failover=failover)
                    return text
                monitor.record_gemini_failure(idx, operation="realtime_query_extract", source="realtime_service", error="empty", latency_ms=int((time.perf_counter() - t0) * 1000))
            except Exception as e:
                monitor.record_gemini_failure(idx, operation="realtime_query_extract", source="realtime_service", error=str(e), is_rate_limit=llm_providers.is_rate_limit_error(e), latency_ms=int((time.perf_counter() - t0) * 1000))
                llm_providers.trip(idx)
                logger.warning("[REALTIME] Gemini query extraction failed (key %d): %s", idx, e)
        return None

    def _race_extract(self, prompt: str) -> Optional[str]:
        """Send to Groq + Gemini together; first non-empty result wins."""
        import concurrent.futures
        monitor = get_api_key_monitor()
        gem_idx = llm_providers.next_key()

        def run_groq():
            return ("groq", 0, self._groq_extract(prompt))

        def run_gemini():
            t0 = time.perf_counter()
            monitor.record_gemini_attempt(gem_idx, operation="realtime_query_extract", source="realtime_service")
            try:
                resp = self._gemini_fast_llms[gem_idx].invoke(prompt)
                text = (resp.content or "").strip()
                if text:
                    monitor.record_gemini_success(gem_idx, operation="realtime_query_extract", source="realtime_service", latency_ms=int((time.perf_counter() - t0) * 1000))
                    return ("gemini", gem_idx, text)
                monitor.record_gemini_failure(gem_idx, operation="realtime_query_extract", source="realtime_service", error="empty", latency_ms=int((time.perf_counter() - t0) * 1000))
                return ("gemini", gem_idx, None)
            except Exception as e:
                monitor.record_gemini_failure(gem_idx, operation="realtime_query_extract", source="realtime_service", error=str(e), is_rate_limit=llm_providers.is_rate_limit_error(e), latency_ms=int((time.perf_counter() - t0) * 1000))
                llm_providers.trip(gem_idx)
                return ("gemini", gem_idx, None)

        winner = None
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            futures = [ex.submit(run_groq), ex.submit(run_gemini)]
            for fut in concurrent.futures.as_completed(futures):
                provider, idx, text = fut.result()
                if text:
                    winner = (provider, idx, text)
                    break
        if winner:
            provider, idx, text = winner
            self._set_extract_provider(provider, idx, race=True)
            return text
        return None

    def _set_extract_provider(self, provider: str, key_index: int, failover: bool = False, race: bool = False) -> None:
        if provider == "gemini":
            label = llm_providers.key_label(key_index)
            pretty = "Gemini"
        else:
            label = "GROQ_API_KEY" if key_index == 0 else "GROQ_API_KEY_" + str(key_index + 1)
            pretty = "Groq"
        if race:
            event = "provider_race"
            message = "Search query race -> " + pretty + " won"
        elif failover:
            event = "provider_failover"
            message = "Search query -> " + pretty + " (" + label + ") [failover]"
        else:
            event = "llm_provider"
            message = "Search query -> " + pretty + " (" + label + ")"
        self.last_provider_event = {
            "event": event,
            "provider": provider,
            "key_index": key_index,
            "key_label": label,
            "operation": "realtime_query_extract",
            "failover": failover,
            "race": race,
            "route": "realtime",
            "message": message,
        }

    def search_serper(self, query: str, num_results: int = 5) -> Tuple[str, Optional[dict]]:
        """Search Google via Serper API with key fallback. Returns (formatted_text, raw_payload)."""

        if not self.serper_api_keys:
            logger.warning("Serper API key not configured. SERPER_API_KEY not set.")
            return ("", None)

        if not query or not str(query).strip():
            return ("", None)

        monitor = get_api_key_monitor()
        t0 = time.perf_counter()
        last_error = None

        for key_idx, api_key in enumerate(self.serper_api_keys):
            try:
                monitor.record_provider_attempt("serper", operation="search", source="realtime_service")

                response = with_retry(
                    lambda k=api_key: http_requests.post(
                        SERPER_API_URL,
                        json={"q": query, "num": num_results},
                        headers={"X-API-KEY": k, "Content-Type": "application/json"},
                        timeout=SERPER_REQUEST_TIMEOUT,
                    ),
                    max_retries=1,
                    initial_delay=0.3,
                )

                if response.status_code == 429 or response.status_code == 403:
                    logger.warning("[SERPER] Key #%d rate limited/forbidden (HTTP %d), trying next...", key_idx + 1, response.status_code)
                    last_error = f"HTTP {response.status_code}"
                    continue

                if response.status_code != 200:
                    logger.error("[SERPER] Key #%d returned status %d: %s", key_idx + 1, response.status_code, response.text[:200])
                    monitor.record_provider_failure("serper", operation="search", source="realtime_service", error=f"HTTP {response.status_code}")
                    last_error = f"HTTP {response.status_code}"
                    continue

                if key_idx > 0:
                    logger.info("[SERPER] Fallback key #%d succeeded", key_idx + 1)

                data = response.json()

                answer_box = data.get("answerBox", {})
                knowledge_graph = data.get("knowledgeGraph", {})
                organic = data.get("organic", [])

                if not answer_box and not organic and not knowledge_graph:
                    logger.warning("No Serper search results for query: %s", query)
                    monitor.record_provider_success("serper", operation="search", source="realtime_service")
                    return ("", None)

                # Build payload for frontend search results widget
                ai_answer = ""
                if answer_box:
                    ab_title = answer_box.get("title", "")
                    ab_answer = answer_box.get("answer", "")
                    ab_snippet = answer_box.get("snippet", "")
                    ai_answer = ab_answer or ab_snippet or ab_title

                payload: Optional[dict] = {
                    "query": query,
                    "answer": ai_answer,
                    "results": [
                        {
                            "title": r.get("title", "No title"),
                            "content": (r.get("snippet") or "")[:300],
                            "url": r.get("link", ""),
                            "score": round(float(r.get("position", 10)) / 10, 2) if r.get("position") else 0.5,
                        }
                        for r in organic[:num_results]
                    ],
                }

                # Build formatted text for LLM context
                parts = [f"=== WEB SEARCH RESULTS FOR: {query} ===\n"]

                if answer_box:
                    ab_parts = []
                    if answer_box.get("title"):
                        ab_parts.append(f"Title: {answer_box['title']}")
                    if answer_box.get("answer"):
                        ab_parts.append(f"Direct Answer: {answer_box['answer']}")
                    if answer_box.get("snippet"):
                        ab_parts.append(f"Snippet: {answer_box['snippet']}")
                    if answer_box.get("source"):
                        ab_parts.append(f"Source: {answer_box['source']}")
                    parts.append("GOOGLE ANSWER BOX (most reliable, use this as primary source):")
                    parts.append("\n".join(ab_parts))
                    parts.append("")

                if knowledge_graph:
                    kg_parts = []
                    if knowledge_graph.get("title"):
                        kg_parts.append(f"Title: {knowledge_graph['title']}")
                    if knowledge_graph.get("type"):
                        kg_parts.append(f"Type: {knowledge_graph['type']}")
                    if knowledge_graph.get("description"):
                        kg_parts.append(f"Description: {knowledge_graph['description']}")
                    for key in ["born", "nationality", "founded", "headquarters", "ceo", "revenue", "height", "weight"]:
                        if knowledge_graph.get(key):
                            kg_parts.append(f"{key.capitalize()}: {knowledge_graph[key]}")
                    if kg_parts:
                        parts.append("KNOWLEDGE GRAPH:")
                        parts.append("\n".join(kg_parts))
                        parts.append("")

                if organic:
                    parts.append("SEARCH RESULTS:")
                    for i, result in enumerate(organic[:num_results], 1):
                        title = result.get("title", "No title")
                        snippet = result.get("snippet", "")
                        link = result.get("link", "")
                        date = result.get("date", "")
                        parts.append(f"\n[Source {i}]")
                        parts.append(f"Title: {title}")
                        if date:
                            parts.append(f"Date: {date}")
                        if snippet:
                            parts.append(f"Content: {snippet}")
                        if link:
                            parts.append(f"URL: {link}")

                parts.append("\n=== END SEARCH RESULTS ===")
                formatted = "\n".join(parts)

                elapsed = time.perf_counter() - t0
                logger.info(
                    "[SERPER] %d organic results, answerBox: %s, knowledgeGraph: %s, formatted: %d chars (%.3fs)",
                    len(organic), "yes" if answer_box else "no",
                    "yes" if knowledge_graph else "no",
                    len(formatted), elapsed,
                )
                monitor.record_provider_success("serper", operation="search", source="realtime_service")
                return (formatted, payload)

            except Exception as e:
                last_error = str(e)
                logger.warning("[SERPER] Key #%d failed: %s", key_idx + 1, e)
                if key_idx < len(self.serper_api_keys) - 1:
                    logger.info("[SERPER] Trying next key...")
                    continue
                break

        # All keys exhausted
        logger.error("[SERPER] All %d Serper API keys failed. Last error: %s", len(self.serper_api_keys), last_error)
        monitor.record_provider_failure("serper", operation="search", source="realtime_service", error=last_error or "all keys failed")
        return ("", None)

    def get_response(self, question: str, chat_history: Optional[List[tuple]] = None, key_start_index: int = 0) -> str:

        try:
            search_query = self._extract_search_query(question, chat_history)
            if getattr(self, "last_provider_event", None):
                yield {"_activity": self.last_provider_event}
            logger.info("[REALTIME] Searching Serper for: %s", " ".join(str(search_query).split())[:80])
            formatted_results, _ = self.search_serper(search_query, num_results=5)

            if formatted_results:
                logger.info("[REALTIME] Serper returned results (length: %d chars)", len(formatted_results))
            else:
                logger.warning("[REALTIME] Serper returned no results for: %s", " ".join(str(search_query).split())[:80])

            extra_parts = [escape_curly_braces(formatted_results)] if formatted_results else [_NO_RESULTS_WARNING]
            prompt, messages = self._build_prompt_and_messages(
                question, chat_history,
                extra_system_parts=extra_parts,
                mode_addendum=REALTIME_CHAT_ADDENDUM,
            )

            t0 = time.perf_counter()
            response_content = self._invoke_llm(prompt, messages, question, key_start_index=key_start_index)
            logger.info("[TIMING] groq_api: %.3fs", time.perf_counter() - t0)
            logger.info(
                "[RESPONSE] Realtime chat | Length: %d chars | Preview: %.120s",
                len(response_content), response_content,
            )
            return response_content

        except AllGroqApisFailedError:
            raise

        except Exception as e:
            logger.error("Error in realtime get_response: %s", e, exc_info=True)
            raise

    def prefetch_web_search(
        self, question: str, chat_history: Optional[List[tuple]] = None
    ) -> Tuple[str, Optional[dict]]:

        try:
            t0 = time.perf_counter()
            search_query = self._extract_search_query(question, chat_history)
            logger.info("[REALTIME] Pre-fetch: extracted query '%s' in %.3fs", search_query[:60], time.perf_counter() - t0)
            formatted_results, payload = self.search_serper(search_query, num_results=5)

            if formatted_results:
                logger.info("[REALTIME] Pre-fetch: Serper returned %d chars in %.3fs total",
                            len(formatted_results), time.perf_counter() - t0)
            return (formatted_results or "", payload)

        except Exception as e:
            logger.warning("[REALTIME] Pre-fetch failed: %s", e)
            return ("", None)

    def stream_response(self, question: str, chat_history: Optional[List[tuple]] = None, key_start_index: int = 0) -> Iterator[Any]:

        try:
            yield {"_activity": {"event": "extracting_query", "message": "Extracting search query..."}}
            search_query = self._extract_search_query(question, chat_history)
            if getattr(self, "last_provider_event", None):
                yield {"_activity": self.last_provider_event}
            logger.info("[REALTIME] Searching Serper for: %s", " ".join(str(search_query).split())[:80])
            yield {"_activity": {"event": "searching_web", "query": search_query, "message": f"Searching Google for: {search_query}"}}

            formatted_results, payload = self.search_serper(search_query, num_results=5)
            num_results = len(payload.get("results", [])) if payload else 0

            if formatted_results:
                logger.info("[REALTIME] Serper returned results (length: %d chars)", len(formatted_results))
                yield {"_activity": {"event": "search_completed", "message": f"Search completed: {num_results} results, {len(formatted_results)} chars of context"}}
            else:
                logger.warning("[REALTIME] Serper returned no results for: %s", " ".join(str(search_query).split())[:80])
                yield {"_activity": {"event": "search_completed", "message": "No search results found"}}

            if payload:
                yield {"_search_results": payload}

            extra_parts = [escape_curly_braces(formatted_results)] if formatted_results else [_NO_RESULTS_WARNING]
            prompt, messages = self._build_prompt_and_messages(
                question, chat_history,
                extra_system_parts=extra_parts,
                mode_addendum=REALTIME_CHAT_ADDENDUM,
            )
            yield from self._stream_llm(prompt, messages, question, key_start_index=key_start_index)
            logger.info("[REALTIME] Stream completed for: %s", " ".join(str(search_query).split())[:80])

        except AllGroqApisFailedError:
            raise

        except Exception as e:
            logger.error("Error in realtime stream_response: %s", e, exc_info=True)
            raise

    def stream_response_with_prefetched(
        self,
        question: str,
        chat_history: Optional[List[tuple]] = None,
        formatted_results: Optional[str] = None,
        payload: Optional[dict] = None,
        key_start_index: int = 0,
    ) -> Iterator[Any]:

        try:
            extra_parts = [escape_curly_braces(formatted_results)] if formatted_results else [_NO_RESULTS_WARNING]
            prompt, messages = self._build_prompt_and_messages(
                question, chat_history,
                extra_system_parts=extra_parts,
                mode_addendum=REALTIME_CHAT_ADDENDUM,
            )
            yield from self._stream_llm(prompt, messages, question, key_start_index=key_start_index)
            logger.info("[REALTIME] Stream completed (pre-fetched results)")

        except AllGroqApisFailedError:
            raise

        except Exception as e:
            logger.error("Error in realtime stream_response_with_prefetched: %s", e, exc_info=True)
            raise
