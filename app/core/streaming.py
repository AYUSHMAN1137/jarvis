"""SSE streaming generator with inline TTS audio synthesis.

Extracted from the old monolithic main.py. Handles sentence splitting,
TTS cache lookups, and base64 audio embedding in the SSE stream.
"""

import json
import time
import re
import hashlib
import base64
import asyncio
import logging
import queue as _queue_mod
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

import edge_tts

from config import TTS_VOICE, TTS_RATE, VOICE_CACHE_DIR

logger = logging.getLogger("J.A.R.V.I.S")

# ---------------------------------------------------------------------------
# Sentence splitting constants
# ---------------------------------------------------------------------------
_SPLIT_RE = re.compile(r"(?<=[.!?,;:])\s+")
_MIN_WORDS_FIRST = 1
_MIN_WORDS = 1
_MERGE_IF_WORDS = 2
_TTS_BUFFER_TIMEOUT = 2.0
_TTS_BUFFER_MIN_WORDS = 4
_ABBREV_HOLD_RE = re.compile(r"^(?:Dr|Mr|Mrs|Ms|Prof|Sr|Jr|St|Vs|Etc)\.$", re.IGNORECASE)


def _should_hold_sentence_for_continuation(sent: str) -> bool:
    t = sent.strip()
    if not t.endswith("."):
        return False
    words = t.split()
    if len(words) != 1:
        return False
    return bool(_ABBREV_HOLD_RE.match(words[0]))


def _split_sentences(buf: str):
    parts = _SPLIT_RE.split(buf)
    if len(parts) <= 1:
        return [], buf

    raw = [p.strip() for p in parts[:-1] if p.strip()]
    sentences, pending = [], ""

    for s in raw:
        if pending:
            s = (pending + " " + s).strip()
            pending = ""
        min_req = _MIN_WORDS_FIRST if not sentences else _MIN_WORDS
        if len(s.split()) < min_req:
            pending = s
            continue
        sentences.append(s)

    remaining = (pending + " " + parts[-1].strip()).strip() if pending else parts[-1].strip()
    return sentences, remaining


def _merge_short(sentences):
    if not sentences:
        return []
    merged, i = [], 0
    while i < len(sentences):
        cur = sentences[i]
        j = i + 1
        while j < len(sentences) and len(sentences[j].split()) <= _MERGE_IF_WORDS:
            cur = (cur + " " + sentences[j]).strip()
            j += 1
        merged.append(cur)
        i = j
    return merged


# ---------------------------------------------------------------------------
# TTS cache helpers
# ---------------------------------------------------------------------------
def tts_cache_key(text: str):
    """Return (cleaned_text, cache_path) for a given TTS input string."""
    cleaned = text.strip().lower()
    cleaned = re.sub(r"[^\w\s\u0900-\u097f]", "", cleaned)  # English + Hindi Unicode
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return None, None
    hash_key = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()
    return cleaned, VOICE_CACHE_DIR / f"{hash_key}.mp3"


def generate_tts_sync(text: str, voice: str, rate: str, activity_q=None) -> bytes:
    """Called from ThreadPoolExecutor. Checks local cache first; synthesizes online on miss.
    Emits tts_cache_hit / tts_cache_miss_saved activity events into activity_q if provided."""
    _, cache_path = tts_cache_key(text)
    if cache_path is None:
        return b""

    short = text[:48]

    # --- Cache Hit: read instantly from SSD ---
    if cache_path.exists():
        try:
            audio = cache_path.read_bytes()
            logger.info("[TTS-CACHE] Hit  '%s'", short)
            if activity_q is not None:
                activity_q.put({"event": "tts_cache_hit", "sentence": short})
            return audio
        except Exception as exc:
            logger.warning("[TTS-CACHE] Read error, will re-synthesize: %s", exc)

    # --- Cache Miss: fetch online, save for future ---
    logger.info("[TTS-CACHE] Miss '%s' -> fetching online...", short)

    async def _inner():
        communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate)
        parts = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                parts.append(chunk["data"])
        audio_data = b"".join(parts)
        if audio_data:
            try:
                VOICE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(audio_data)
                logger.info("[TTS-CACHE] Saved '%s'", short)
                if activity_q is not None:
                    activity_q.put({"event": "tts_cache_miss_saved", "sentence": short})
            except Exception as exc:
                logger.warning("[TTS-CACHE] Save failed: %s", exc)
        return audio_data

    return asyncio.run(_inner())


# Thread pool for TTS synthesis (shared across all streaming routes)
tts_pool = ThreadPoolExecutor(max_workers=4)


# ---------------------------------------------------------------------------
# Main SSE stream generator
# ---------------------------------------------------------------------------
def stream_generator(session_id: str, chunk_iter, is_realtime: bool, tts_enabled: bool = False):
    yield f"data: {json.dumps({'session_id': session_id, 'chunk': '', 'done': False})}\n\n"
    buffer = ""
    held = None
    is_first = True
    audio_queue = []
    last_submit_time = time.perf_counter()
    # Thread-safe queue for TTS cache activity events emitted from the thread pool
    tts_activity_q = _queue_mod.Queue() if tts_enabled else None

    def _submit(text):
        nonlocal last_submit_time
        if not text or not text.strip():
            return
        audio_queue.append((
            tts_pool.submit(generate_tts_sync, text, TTS_VOICE, TTS_RATE, tts_activity_q),
            text
        ))
        last_submit_time = time.perf_counter()

    def _drain_tts_activity():
        """Drain any pending TTS cache activity events and return as SSE strings."""
        events = []
        if tts_activity_q is None:
            return events
        while True:
            try:
                act = tts_activity_q.get_nowait()
                events.append(f"data: {json.dumps({'activity': act})}\n\n")
            except _queue_mod.Empty:
                break
        return events

    def _drain_ready():
        events = []
        # First flush any pending cache-activity events
        events.extend(_drain_tts_activity())
        while audio_queue and audio_queue[0][0].done():
            fut, sent = audio_queue.pop(0)
            try:
                audio = fut.result()
                b64 = base64.b64encode(audio).decode("ascii")
                events.append(f"data: {json.dumps({'audio': b64, 'sentence': sent})}\n\n")
            except Exception as exc:
                logger.warning("[TTS-INLINE] Failed for '%s': %s", sent[:40], exc)
        return events

    def _yield_completed_audio():
        if not tts_enabled:
            return
        for ev in _drain_ready():
            yield ev

    try:
        for chunk in chunk_iter:
            if isinstance(chunk, dict) and "_activity" in chunk:
                yield f"data: {json.dumps({'activity': chunk['_activity']})}\n\n"
                yield from _yield_completed_audio()
                continue

            if isinstance(chunk, dict) and "_search_results" in chunk:
                yield f"data: {json.dumps({'search_results': chunk['_search_results']})}\n\n"
                yield from _yield_completed_audio()
                continue

            if isinstance(chunk, dict) and "_actions" in chunk:
                yield f"data: {json.dumps({'actions': chunk['_actions']})}\n\n"
                yield from _yield_completed_audio()
                continue

            if isinstance(chunk, dict) and "_background_tasks" in chunk:
                yield f"data: {json.dumps({'background_tasks': chunk['_background_tasks']})}\n\n"
                yield from _yield_completed_audio()
                continue

            if not chunk:
                yield from _yield_completed_audio()
                continue

            yield f"data: {json.dumps({'chunk': chunk, 'done': False})}\n\n"

            if not tts_enabled:
                continue

            yield from _yield_completed_audio()

            buffer += chunk
            sentences, buffer = _split_sentences(buffer)
            sentences = _merge_short(sentences)

            if held and sentences and len(sentences[0].split()) <= _MERGE_IF_WORDS:
                held = (held + " " + sentences[0]).strip()
                sentences = sentences[1:]

            for i, sent in enumerate(sentences):
                min_w = _MIN_WORDS_FIRST if is_first else _MIN_WORDS
                if len(sent.split()) < min_w:
                    continue

                is_last = (i == len(sentences) - 1)

                if held:
                    _submit(held)
                    held = None
                    is_first = False

                if is_last and _should_hold_sentence_for_continuation(sent):
                    held = sent
                else:
                    _submit(sent)
                    is_first = False

            if buffer and len(buffer.split()) >= _TTS_BUFFER_MIN_WORDS:
                if time.perf_counter() - last_submit_time > _TTS_BUFFER_TIMEOUT:
                    if held:
                        _submit(held)
                        held = None
                        is_first = False
                    _submit(buffer.strip())
                    buffer = ""
                    is_first = False

            yield from _yield_completed_audio()

    except Exception as e:
        for fut, _ in audio_queue:
            fut.cancel()
        yield f"data: {json.dumps({'chunk': '', 'done': True, 'error': str(e)})}\n\n"
        return

    if tts_enabled:
        remaining = buffer.strip()
        if held:
            if remaining and len(remaining.split()) <= _MERGE_IF_WORDS:
                _submit((held + " " + remaining).strip())
            else:
                _submit(held)
                if remaining:
                    _submit(remaining)
        elif remaining:
            _submit(remaining)

        for fut, sent in audio_queue:
            try:
                audio = fut.result(timeout=15)
                b64 = base64.b64encode(audio).decode("ascii")
                yield f"data: {json.dumps({'audio': b64, 'sentence': sent})}\n\n"
            except FuturesTimeoutError:
                logger.warning("[TTS-INLINE] Timeout for '%s' (15s)", (sent or "")[:40])
            except Exception as exc:
                logger.warning("[TTS-INLINE] Failed for '%s': %s", (sent or "")[:40], exc)

    yield f"data: {json.dumps({'chunk': '', 'done': True, 'session_id': session_id})}\n\n"
