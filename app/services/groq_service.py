from typing import List, Optional, Iterator
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage
import logging
import time
from app.services.vector_store import VectorStoreService
from app.services.api_key_monitor import get_api_key_monitor
from app.utils.time_info import get_time_information
from app.utils.retry import with_retry
from app.services import llm_providers
from config import (
    GROQ_API_KEYS,
    GROQ_MODEL,
    JARVIS_SYSTEM_PROMPT,
    GENERAL_CHAT_ADDENDUM,
)

logger = logging.getLogger("J.A.R.V.I.S")
GROQ_REQUEST_TIMEOUT = 60

ALL_APIS_FAILED_MESSAGE = (
    "I'm unable to process your request at the moment. All API services are "
    "temporarily unavailable. Please try again in a few minutes."
)

class AllGroqApisFailedError(Exception):
    pass

def escape_curly_braces(text: str) -> str:
    if not text:
        return text
    return text.replace("{", "{{").replace("}", "}}")

_REPEAT_WINDOW = 100
_REPEAT_THRESHOLD = 3
_REPEAT_CHECK_INTERVAL = 200

def _detect_repetition_loop(text: str) -> bool:

    if len(text) < _REPEAT_WINDOW * _REPEAT_THRESHOLD:
        return False
    
    phrase = text[-_REPEAT_WINDOW:]
    return text.count(phrase) >= _REPEAT_THRESHOLD

def _truncate_at_repetition(text: str) -> str:

    if len(text) < _REPEAT_WINDOW * _REPEAT_THRESHOLD:
        return text

    phrase = text[-_REPEAT_WINDOW:]
    if text.count(phrase) < _REPEAT_THRESHOLD:
        return text

    first = text.find(phrase)
    second = text.find(phrase, first + 1)

    if second > first:
        return text[:second].rstrip()
    return text

def _is_rate_limit_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "429" in str(exc) or "rate limit" in msg or "tokens per day" in msg

def _log_timing(label: str, elapsed: float, extra: str = ""):
    msg = f"[TIMING] {label}: {elapsed:.3f}s"

    if extra:
        msg += f" ({extra})"
    logger.info(msg)

def _mask_api_key(key: str) -> str:

    if not key or len(key) <= 12:
        return "***masked***"
    return f"{key[:8]}...{key[-4:]}"

class GroqService:
    
    def __init__(self, vector_store_service: VectorStoreService):

        if not GROQ_API_KEYS:
            raise ValueError(
                "No Groq API keys configured. Set GROQ_API_KEY (and optionally GROQ_API_KEY_2, GROQ_API_KEY_3, ...) in .env"
            )

        self.llms = [
            ChatGroq(
                groq_api_key=key,
                model_name=GROQ_MODEL,
                temperature=0.5,
                max_tokens=1024,
                request_timeout=GROQ_REQUEST_TIMEOUT,
                model_kwargs={"frequency_penalty": 0.3},
            )
            for key in GROQ_API_KEYS
        ]

        self.vector_store_service = vector_store_service
        logger.info(f"Initialized GroqService with {len(GROQ_API_KEYS)} API key(s) (primary-first fallback)")

        # Optional Gemini secondary provider (OpenAI-compatible). Built only when
        # Gemini is enabled + keys exist. When empty, every code path below behaves
        # exactly like the original Groq-only flow (zero regression).
        self.gemini_llms = []
        self.last_provider_event = None
        if llm_providers.gemini_enabled():
            try:
                self.gemini_llms = [
                    llm_providers.make_langchain_llm(idx, temperature=0.5, max_tokens=1024)
                    for idx in range(llm_providers.key_count())
                ]
                logger.info(f"[GEMINI] GroqService secondary provider ready with {len(self.gemini_llms)} key(s)")
            except Exception as e:
                logger.warning(f"[GEMINI] Could not initialize Gemini fallback in GroqService: {e}")
                self.gemini_llms = []

    def _invoke_llm(
        self,
        prompt: ChatPromptTemplate,
        messages: list,
        question: str,
        key_start_index: int = 0,
    ) -> str:
        
        n = len(self.llms)
        last_exc = None
        keys_tried = []
        monitor = get_api_key_monitor()

        for j in range(n):
            i = (key_start_index + j) % n
            keys_tried.append(i)
            masked_key = _mask_api_key(GROQ_API_KEYS[i])
            logger.info(f"Trying API key #{i + 1}/{n}: {masked_key}")

            monitor.record_groq_attempt(i, operation="chat_invoke", source="groq_service")
            key_t0 = time.perf_counter()

            def _invoke_with_key():
                chain = prompt | self.llms[i]
                return chain.invoke({"history": messages, "question": question})

            try:
                response = with_retry(
                    _invoke_with_key,
                    max_retries=2,
                    initial_delay=0.5,
                )

                if i > 0:
                    logger.info(f"Fallback successful: API key #{i + 1}/{n} succeeded: {masked_key}")

                text = response.content

                if _detect_repetition_loop(text):
                    logger.warning("[INVOKE] Repetition loop detected — truncating response (%d chars)", len(text))
                    text = _truncate_at_repetition(text)
                
                latency_ms = int((time.perf_counter() - key_t0) * 1000)
                monitor.record_groq_success(i, operation="chat_invoke", source="groq_service", latency_ms=latency_ms)
                
                return text
            
            except Exception as e:
                last_exc = e
                latency_ms = int((time.perf_counter() - key_t0) * 1000)

                if _is_rate_limit_error(e):
                    logger.warning(f"API key #{i + 1}/{n} rate limited: {masked_key}")
                    monitor.record_groq_failure(i, operation="chat_invoke", source="groq_service", error=str(e), is_rate_limit=True, latency_ms=latency_ms)

                else:
                    logger.warning(f"API key #{i + 1}/{n} failed: {masked_key} - {str(e)[:100]}")
                    monitor.record_groq_failure(i, operation="chat_invoke", source="groq_service", error=str(e), is_rate_limit=False, latency_ms=latency_ms)

                if i < n - 1:
                    logger.info(f"Falling back to next API key...")
                    continue

                break

        # Groq keys exhausted — try Gemini as a secondary provider (if enabled).
        gem_text = self._invoke_gemini(prompt, messages, question, operation="chat_invoke")
        if gem_text is not None:
            return gem_text
        masked_all = ", ".join([_mask_api_key(GROQ_API_KEYS[j]) for j in keys_tried])
        logger.error(f"All {n} API key(s) failed. Tried: {masked_all}")
        raise AllGroqApisFailedError(ALL_APIS_FAILED_MESSAGE) from last_exc

    def _stream_llm(
        self,
        prompt: ChatPromptTemplate,
        messages: list,
        question: str,
        key_start_index: int = 0,
    ) -> Iterator[str]:
        
        n = len(self.llms)
        last_exc = None
        monitor = get_api_key_monitor()

        for j in range(n):
            i = (key_start_index + j) % n
            masked_key = _mask_api_key(GROQ_API_KEYS[i])
            logger.info(f"Streaming with API key #{i + 1}/{n}: {masked_key}")

            monitor.record_groq_attempt(i, operation="chat_stream", source="groq_service")
            key_t0 = time.perf_counter()

            try:
                chain = prompt | self.llms[i]
                chunk_count = 0
                first_chunk_time = None
                stream_start = time.perf_counter()
                accumulated = ""
                last_check_len = 0
                repetition_stopped = False

                for chunk in chain.stream({"history": messages, "question": question}):
                    content = ""

                    if hasattr(chunk, "content"):
                        content = chunk.content or ""

                    elif isinstance(chunk, dict) and "content" in chunk:
                        content = chunk.get("content", "") or ""

                    if isinstance(content, str) and content:

                        if first_chunk_time is None:
                            first_chunk_time = time.perf_counter() - stream_start
                            _log_timing("first_chunk", first_chunk_time)
                            yield self._provider_activity("groq", i, failover=(j > 0), operation="chat_stream")

                        chunk_count += 1
                        accumulated += content

                        if len(accumulated) - last_check_len >= _REPEAT_CHECK_INTERVAL:
                            last_check_len = len(accumulated)

                            if _detect_repetition_loop(accumulated):
                                logger.warning("[STREAM] Repetition loop detected after %d chars — stopping", len(accumulated))
                                repetition_stopped = True
                                break

                        yield content

                total_stream = time.perf_counter() - stream_start
                _log_timing("groq_stream_total", total_stream, f"chunks: {chunk_count}{', TRUNCATED-REPETITION' if repetition_stopped else ''}")

                if i > 0 and chunk_count > 0:
                    logger.info(f"Fallback successful: API key #{i + 1}/{n} streamed: {masked_key}")
                
                latency_ms = int((time.perf_counter() - key_t0) * 1000)
                monitor.record_groq_success(i, operation="chat_stream", source="groq_service", latency_ms=latency_ms)
                return

            except Exception as e:

                last_exc = e
                latency_ms = int((time.perf_counter() - key_t0) * 1000)
                if _is_rate_limit_error(e):
                    logger.warning(f"API key #{i + 1}/{n} rate limited: {masked_key}")
                    monitor.record_groq_failure(i, operation="chat_stream", source="groq_service", error=str(e), is_rate_limit=True, latency_ms=latency_ms)

                else:
                    logger.warning(f"API key #{i + 1}/{n} failed: {masked_key} - {str(e)[:100]}")
                    monitor.record_groq_failure(i, operation="chat_stream", source="groq_service", error=str(e), is_rate_limit=False, latency_ms=latency_ms)

                if i < n - 1:
                    logger.info("Falling back to next API key for stream...")
                    continue
                break

        # Groq keys exhausted — try Gemini as a secondary provider (if enabled).
        gem_ok = yield from self._stream_gemini(prompt, messages, question, operation="chat_stream")
        if gem_ok:
            return
        logger.error(f"All {n} API key(s) failed during stream (Gemini fallback unavailable or also failed).")
        raise AllGroqApisFailedError(ALL_APIS_FAILED_MESSAGE) from last_exc

    def _provider_activity(self, provider, key_index, failover=False, operation="chat", route="chat"):
        """Build an activity event describing which provider/key answered."""
        if provider == "gemini":
            label = "GEMINI_API_KEY" if key_index == 0 else f"GEMINI_API_KEY_{key_index + 1}"
            pretty = "Gemini"
        else:
            label = "GROQ_API_KEY" if key_index == 0 else f"GROQ_API_KEY_{key_index + 1}"
            pretty = "Groq"
        if failover:
            message = f"{pretty} ({label}) \u2192 answered (failover)"
            event = "provider_failover"
        else:
            message = f"{pretty} ({label}) \u2192 answered"
            event = "llm_provider"
        ev = {
            "event": event,
            "provider": provider,
            "key_index": key_index,
            "key_label": label,
            "operation": operation,
            "failover": bool(failover),
            "message": message,
            "route": route,
        }
        self.last_provider_event = ev
        return {"_activity": ev}

    def _stream_gemini(self, prompt, messages, question, operation="chat_stream"):
        """Stream from Gemini keys (failover order). Returns True if any content was produced."""
        if not self.gemini_llms:
            return False
        monitor = get_api_key_monitor()
        for idx in llm_providers.ordered_keys():
            if idx >= len(self.gemini_llms):
                continue
            monitor.record_gemini_attempt(idx, operation=operation, source="groq_service")
            key_t0 = time.perf_counter()
            emitted = False
            try:
                chain = prompt | self.gemini_llms[idx]
                chunk_count = 0
                accumulated = ""
                last_check_len = 0
                for chunk in chain.stream({"history": messages, "question": question}):
                    content = ""
                    if hasattr(chunk, "content"):
                        content = chunk.content or ""
                    elif isinstance(chunk, dict) and "content" in chunk:
                        content = chunk.get("content", "") or ""
                    if isinstance(content, str) and content:
                        if not emitted:
                            emitted = True
                            yield self._provider_activity("gemini", idx, failover=True, operation=operation)
                        chunk_count += 1
                        accumulated += content
                        if len(accumulated) - last_check_len >= _REPEAT_CHECK_INTERVAL:
                            last_check_len = len(accumulated)
                            if _detect_repetition_loop(accumulated):
                                logger.warning("[GEMINI-STREAM] Repetition loop detected — stopping")
                                break
                        yield content
                latency_ms = int((time.perf_counter() - key_t0) * 1000)
                if chunk_count > 0:
                    monitor.record_gemini_success(idx, operation=operation, source="groq_service", latency_ms=latency_ms)
                    logger.info(f"[GEMINI] Fallback stream succeeded on key #{idx + 1}")
                    return True
                monitor.record_gemini_failure(idx, operation=operation, source="groq_service", error="empty stream", latency_ms=latency_ms)
            except Exception as e:
                latency_ms = int((time.perf_counter() - key_t0) * 1000)
                rl = llm_providers.is_rate_limit_error(e)
                monitor.record_gemini_failure(idx, operation=operation, source="groq_service", error=str(e), is_rate_limit=rl, latency_ms=latency_ms)
                llm_providers.trip(idx)
                logger.warning(f"[GEMINI] key #{idx + 1} stream failed: {str(e)[:100]}")
                if emitted:
                    # Partial content already streamed; do not retry (would duplicate text).
                    return True
                continue
        return False

    def _invoke_gemini(self, prompt, messages, question, operation="chat_invoke"):
        """Non-streaming Gemini fallback. Returns text or None."""
        if not self.gemini_llms:
            return None
        monitor = get_api_key_monitor()
        for idx in llm_providers.ordered_keys():
            if idx >= len(self.gemini_llms):
                continue
            monitor.record_gemini_attempt(idx, operation=operation, source="groq_service")
            key_t0 = time.perf_counter()
            try:
                chain = prompt | self.gemini_llms[idx]
                response = chain.invoke({"history": messages, "question": question})
                text = response.content
                if _detect_repetition_loop(text):
                    text = _truncate_at_repetition(text)
                latency_ms = int((time.perf_counter() - key_t0) * 1000)
                monitor.record_gemini_success(idx, operation=operation, source="groq_service", latency_ms=latency_ms)
                self._provider_activity("gemini", idx, failover=True, operation=operation)
                logger.info(f"[GEMINI] Fallback invoke succeeded on key #{idx + 1}")
                return text
            except Exception as e:
                latency_ms = int((time.perf_counter() - key_t0) * 1000)
                rl = llm_providers.is_rate_limit_error(e)
                monitor.record_gemini_failure(idx, operation=operation, source="groq_service", error=str(e), is_rate_limit=rl, latency_ms=latency_ms)
                llm_providers.trip(idx)
                logger.warning(f"[GEMINI] key #{idx + 1} invoke failed: {str(e)[:100]}")
                continue
        return None

    def _build_prompt_and_messages(
        self,
        question: str,
        chat_history: Optional[List[tuple]] = None,
        extra_system_parts: Optional[List[str]] = None,
        mode_addendum: str = "",
    ) -> tuple:
        
        context = ""
        context_sources = []
        t0 = time.perf_counter()

        try:
            retriever = self.vector_store_service.get_retriever(k=5)

            context_docs = retriever.invoke(question)

            if context_docs:
                context = "\n".join([doc.page_content for doc in context_docs])

                context_sources = [doc.metadata.get("source", "unknown") for doc in context_docs]
                logger.info("[CONTEXT] Retrieved %d chunks from sources: %s", len(context_docs), context_sources)
            
            else:
                logger.info("[CONTEXT] No relevant chunks found for query")

        except Exception as retrieval_err:
            logger.warning("Vector store retrieval failed, using empty context: %s", retrieval_err)

        finally:
            _log_timing("vector_db", time.perf_counter() - t0)

        time_info = get_time_information()
        system_message = JARVIS_SYSTEM_PROMPT

        # Phase 2 persistent memory: prepend what JARVIS remembers about the user
        # (profile, facts, last action, corrections). Fail-soft via the helper.
        from app.services.memory_service import augment_system_prompt
        system_message = augment_system_prompt(system_message, question)

        # Phase 8 personalization: append learned facts/aliases/habits so the
        # agent's answers reflect what JARVIS knows about this user. Fail-soft.
        try:
            from app.services.agent.phase8 import get_phase8
            system_message = get_phase8().augment(system_message)
        except Exception as _p8aug:  # noqa: BLE001
            logger.debug("[MEMORY] Phase 8 augment skipped: %s", _p8aug)

        system_message += f"\n\nCurrent time and date: {time_info}"

        if context:
            system_message += f"\n\nRelevant context from your learning data and past conversations:\n{escape_curly_braces(context)}"

        if extra_system_parts:
            system_message += "\n\n" + "\n\n".join(extra_system_parts)

        if mode_addendum:
            system_message += f"\n\n{mode_addendum}"

        prompt = ChatPromptTemplate.from_messages([
            ("system", system_message),
            MessagesPlaceholder(variable_name="history"),
            ("human", "{question}"),
        ])

        messages = []

        if chat_history:
            for human_msg, ai_msg in chat_history:
                messages.append(HumanMessage(content=human_msg))
                messages.append(AIMessage(content=ai_msg))

        logger.info("[PROMPT] System message length: %d chars | History pairs: %d | Question: %.100s",
                     len(system_message), len(chat_history) if chat_history else 0, question)

        return prompt, messages

    def get_response(
        self,
        question: str,
        chat_history: Optional[List[tuple]] = None,
        key_start_index: int = 0,
    ) -> str:
        
        try:
            prompt, messages = self._build_prompt_and_messages(
                question, chat_history, mode_addendum=GENERAL_CHAT_ADDENDUM,
            )
            t0 = time.perf_counter()
            result = self._invoke_llm(prompt, messages, question, key_start_index=key_start_index)
            _log_timing("groq_api", time.perf_counter() - t0)
            logger.info("[RESPONSE] General chat | Length: %d chars | Preview: %.120s", len(result), result)
            return result
        
        except AllGroqApisFailedError:
            raise

        except Exception as e:
            raise Exception(f"Error getting response from Groq: {str(e)}") from e

    def stream_response(
        self,
        question: str,
        chat_history: Optional[List[tuple]] = None,
        key_start_index: int = 0,
    ) -> Iterator[str]:
        
        try:
            prompt, messages = self._build_prompt_and_messages(
                question, chat_history, mode_addendum=GENERAL_CHAT_ADDENDUM,
            )
            yield {"_activity": {"event": "context_retrieved", "message": "Retrieved relevant context from knowledge base"}}
            yield from self._stream_llm(prompt, messages, question, key_start_index=key_start_index)
        
        except AllGroqApisFailedError:
            raise
        
        except Exception as e:
            raise Exception(f"Error streaming response from Groq: {str(e)}") from e
