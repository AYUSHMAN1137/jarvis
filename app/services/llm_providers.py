"""
Gemini provider pool: key rotation + per-key circuit breaker + client factories.

Gemini is reached through its OpenAI-compatible endpoint, which means the same
OpenAI-style code paths we already use for Groq work unchanged:
  - LangChain code (chat / realtime / brain) uses ChatOpenAI(base_url=...).
  - The agent's raw native tool-calling loop uses openai.OpenAI(base_url=...).

This module is import-safe even if the optional deps (langchain-openai / openai)
are not installed: the heavy imports happen lazily inside the factory helpers,
so simply importing this module never crashes the app. Callers should gate on
`gemini_enabled()` before building clients.

Design goals (mirrors app/utils/key_rotation.py + the Groq fallback style):
  - Round-robin across many Gemini keys (built for 15-20+ keys).
  - Per-key circuit breaker: a key that hard-fails / rate-limits is skipped for
    a short cooldown instead of being retried on every request.
  - Never raise on bad input; return safe defaults.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, List, Optional

from config import (
    GEMINI_API_KEYS,
    ENABLE_GEMINI_FALLBACK,
    ENABLE_RACE_MODE,
    GEMINI_OPENAI_BASE_URL,
    GEMINI_MODEL,
    GEMINI_REQUEST_TIMEOUT,
    PROVIDER_COOLDOWN_SECONDS,
    ZAI_API_KEYS,
    ENABLE_ZAI_AGENT,
    ZAI_OPENAI_BASE_URL,
    ZAI_REQUEST_TIMEOUT,
)

logger = logging.getLogger("J.A.R.V.I.S")

_counter = 0
_lock = threading.Lock()
# key_index -> epoch seconds until which the key stays in cooldown
_cooldown_until: dict = {}


def gemini_enabled() -> bool:
    """True only when Gemini failover is switched on AND at least one key exists."""
    return bool(ENABLE_GEMINI_FALLBACK and GEMINI_API_KEYS)


def race_enabled() -> bool:
    """True when race mode is on (implies Gemini is enabled)."""
    return bool(ENABLE_RACE_MODE and gemini_enabled())


def key_count() -> int:
    return len(GEMINI_API_KEYS)


def key_label(idx: int) -> str:
    return "GEMINI_API_KEY" if idx == 0 else f"GEMINI_API_KEY_{idx + 1}"


def trip(key_index: int, seconds: Optional[float] = None) -> None:
    """Put a key into cooldown (circuit breaker) after a hard failure."""
    if seconds is None:
        seconds = PROVIDER_COOLDOWN_SECONDS
    with _lock:
        _cooldown_until[key_index] = time.time() + max(1.0, float(seconds))


def _in_cooldown(key_index: int, now: float) -> bool:
    return _cooldown_until.get(key_index, 0.0) > now


def next_key() -> int:
    """Round-robin index, skipping keys currently in cooldown when possible."""
    global _counter
    n = len(GEMINI_API_KEYS)
    if n <= 0:
        return 0
    now = time.time()
    with _lock:
        start = _counter % n
        _counter += 1
    for off in range(n):
        idx = (start + off) % n
        if not _in_cooldown(idx, now):
            return idx
    return start


def ordered_keys(start_index: Optional[int] = None) -> List[int]:
    """Full failover order: live keys first (from start_index), cooled keys last."""
    n = len(GEMINI_API_KEYS)
    if n <= 0:
        return []
    if start_index is None:
        start_index = next_key()
    now = time.time()
    seq = [(start_index + off) % n for off in range(n)]
    live = [i for i in seq if not _in_cooldown(i, now)]
    cooled = [i for i in seq if _in_cooldown(i, now)]
    return live + cooled


def make_langchain_llm(
    key_index: int,
    *,
    model: Optional[str] = None,
    temperature: float = 0.5,
    max_tokens: int = 1024,
    timeout: Optional[int] = None,
) -> Any:
    """Build a LangChain chat model backed by Gemini's OpenAI-compatible API.

    Raises ImportError if langchain-openai is not installed; callers that reach
    here have already checked gemini_enabled() and should catch failures.
    """
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model or GEMINI_MODEL,
        api_key=GEMINI_API_KEYS[key_index],
        base_url=GEMINI_OPENAI_BASE_URL,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout if timeout is not None else GEMINI_REQUEST_TIMEOUT,
        max_retries=0,
    )


def make_raw_client(key_index: int, timeout: Optional[int] = None) -> Any:
    """Build a raw OpenAI-SDK client pointed at Gemini (for native tool-calling)."""
    from openai import OpenAI

    return OpenAI(
        api_key=GEMINI_API_KEYS[key_index],
        base_url=GEMINI_OPENAI_BASE_URL,
        timeout=float(timeout if timeout is not None else GEMINI_REQUEST_TIMEOUT),
        max_retries=0,
    )


def is_rate_limit_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "429" in str(exc) or "rate limit" in msg or "quota" in msg or "resource_exhausted" in msg


# === Z.ai (GLM) provider pool: PRIMARY agent model ===
# Mirrors the Gemini pool above (round-robin + per-key circuit breaker) but for
# GLM-4.7-Flash on Z.ai's OpenAI-compatible endpoint. Kept as a separate pool
# with its own counter/cooldown state so GLM and Gemini circuit breakers never
# interfere with each other.
_zai_counter = 0
_zai_cooldown_until: dict = {}


def zai_enabled() -> bool:
    """True only when GLM/Z.ai is switched on AND at least one key exists."""
    return bool(ENABLE_ZAI_AGENT and ZAI_API_KEYS)


def zai_key_count() -> int:
    return len(ZAI_API_KEYS)


def zai_key_label(idx: int) -> str:
    return "ZAI_API_KEY" if idx == 0 else f"ZAI_API_KEY_{idx + 1}"


def zai_trip(key_index: int, seconds: Optional[float] = None) -> None:
    """Put a Z.ai key into cooldown (circuit breaker) after a hard failure."""
    if seconds is None:
        seconds = PROVIDER_COOLDOWN_SECONDS
    with _lock:
        _zai_cooldown_until[key_index] = time.time() + max(1.0, float(seconds))


def _zai_in_cooldown(key_index: int, now: float) -> bool:
    return _zai_cooldown_until.get(key_index, 0.0) > now


def zai_next_key() -> int:
    """Round-robin index across Z.ai keys, skipping cooled keys when possible."""
    global _zai_counter
    n = len(ZAI_API_KEYS)
    if n <= 0:
        return 0
    now = time.time()
    with _lock:
        start = _zai_counter % n
        _zai_counter += 1
    for off in range(n):
        idx = (start + off) % n
        if not _zai_in_cooldown(idx, now):
            return idx
    return start


def zai_ordered_keys(start_index: Optional[int] = None) -> List[int]:
    """Full Z.ai failover order: live keys first, cooled keys last."""
    n = len(ZAI_API_KEYS)
    if n <= 0:
        return []
    if start_index is None:
        start_index = zai_next_key()
    now = time.time()
    seq = [(start_index + off) % n for off in range(n)]
    live = [i for i in seq if not _zai_in_cooldown(i, now)]
    cooled = [i for i in seq if _zai_in_cooldown(i, now)]
    return live + cooled


def make_zai_raw_client(key_index: int, timeout: Optional[int] = None) -> Any:
    """Build a raw OpenAI-SDK client pointed at Z.ai (for native tool-calling)."""
    from openai import OpenAI

    return OpenAI(
        api_key=ZAI_API_KEYS[key_index],
        base_url=ZAI_OPENAI_BASE_URL,
        timeout=float(timeout if timeout is not None else ZAI_REQUEST_TIMEOUT),
        max_retries=0,
    )
