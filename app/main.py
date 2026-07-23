from pathlib import Path
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, RedirectResponse, Response, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from contextlib import asynccontextmanager
import uvicorn
import logging
import json
import time
import re
import hashlib
import base64
import asyncio
import queue as _queue_mod
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
import edge_tts
from app.models import ChatRequest, ChatResponse, TTSRequest

RATE_LIMIT_MESSAGE = (
    "You've reached your daily API limit for this assistant. "
    "Your credits will reset in a few hours, or you can upgrade your plan for more. "
    "Please try again later."
)

def _is_rate_limit_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "429" in str(exc) or "rate limit" in msg or "tokens per day" in msg

from app.services.vector_store import VectorStoreService
from app.services.groq_service import GroqService, AllGroqApisFailedError
from app.services.realtime_service import RealtimeGroqService
from app.services.chat_service import ChatService
from app.services.brain_service import BrainService
from app.services.vision_service import VisionService
from app.services.api_key_monitor import get_api_key_monitor
from app.services.agent.agent_loop import AgentLoop
from app.services.agent.tools import load_all_tools
from app.services.agent import deps as agent_deps
from app.services.skills.gmail_service import GmailService
from app.services.skills.calendar_service import CalendarService
from app.services.skills.drive_service import DriveService

from config import (
    VECTOR_STORE_DIR, GROQ_API_KEYS, GROQ_MODEL, SERPER_API_KEY,
    EMBEDDING_MODEL, CHUNK_SIZE, CHUNK_OVERLAP, MAX_CHAT_HISTORY_TURNS,
    ASSISTANT_NAME, TTS_VOICE, TTS_RATE, VOICE_CACHE_DIR,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger("J.A.R.V.I.S")

# Silence the harmless comtypes COM-release access violation that pycaw/pywinauto
# objects raise from __del__ during GC on a non-owning thread. Must run before
# any COM object is created (i.e. before the watcher / audio / UIA start).
try:
    from app._com_guard import install_com_release_guard

    install_com_release_guard()
except Exception as _cg_err:  # noqa: BLE001 - never block startup on this
    logger.debug("[STARTUP] COM-release guard not installed: %s", _cg_err)

# Quiet noisy third-party loggers so the J.A.R.V.I.S logs stay clean and readable.
# (httpx logs every HTTP call as "HTTP/1.1 200 OK", sentence-transformers prints
# progress bars, uvicorn's reloader chatters, etc. -- none of that is useful here.)
for _noisy in (
    "httpx", "httpcore", "urllib3", "openai", "groq",
    "sentence_transformers", "transformers", "huggingface_hub",
    "langchain", "langchain_community", "langchain_core", "faiss", "filelock",
    "watchfiles", "watchfiles.main", "python_multipart", "multipart", "PIL",
):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# screen_brightness_control spams a WARNING ("EDIDParseError ... invalid display
# name") every few seconds on some laptop panels. It's harmless and not
# actionable, so silence anything below ERROR for it.
logging.getLogger("screen_brightness_control").setLevel(logging.ERROR)
# uvicorn writes its own access line for EVERY request, which just duplicates
# our cleaner [REQUEST] log -- keep only warnings/errors from it.
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

vector_store_service: VectorStoreService = None
groq_service: GroqService = None
realtime_service: RealtimeGroqService = None
brain_service: BrainService = None
vision_service: VisionService = None
agent_loop: AgentLoop = None
chat_service: ChatService = None

def print_title():

    title = """

   ╔══════════════════════════════════════════════════════════╗
   ║                                                          ║
   ║         ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗          ║
   ║         ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝          ║
   ║         ██║███████║██████╔╝██║   ██║██║███████╗          ║
   ║    ██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║          ║
   ║    ╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║          ║
   ║     ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝          ║
   ║                                                          ║
   ║          Just A Rather Very Intelligent System           ║
   ║                                                          ║
   ╚══════════════════════════════════════════════════════════╝

    """
    print(title)

@asynccontextmanager

async def lifespan(app: FastAPI):

    global vector_store_service, groq_service, realtime_service, brain_service
    global vision_service, agent_loop, chat_service
    print_title()
    logger.info("=" * 60)
    logger.info("J.A.R.V.I.S - Starting Up...")
    logger.info("=" * 60)
    logger.info("[CONFIG] Assistant name: %s", ASSISTANT_NAME)
    logger.info("[CONFIG] Groq model: %s", GROQ_MODEL)
    logger.info("[CONFIG] Groq API keys loaded: %d", len(GROQ_API_KEYS))
    logger.info("[CONFIG] Serper API key: %s", "configured" if SERPER_API_KEY else "NOT SET")
    logger.info("[CONFIG] Image generation: Pollinations.ai (free, no API key)")
    logger.info("[CONFIG] Embedding model: %s", EMBEDDING_MODEL)
    logger.info("[CONFIG] Chunk size: %d | Overlap: %d | Max history turns: %d",
                CHUNK_SIZE, CHUNK_OVERLAP, MAX_CHAT_HISTORY_TURNS)

    try:
        _t_start = time.perf_counter()
        from app.services import llm_providers

        # Start the background System State Watcher as early as possible so it
        # begins tracking processes/windows before the first command arrives.
        # Non-fatal: if it can't start, JARVIS keeps working the old way.
        try:
            from app.services.watcher import get_watcher
            get_watcher().start()
            logger.info("[STARTUP] System state watcher (background daemon) started.")
        except Exception as _we:  # noqa: BLE001
            logger.warning("[STARTUP] Watcher could not start (non-fatal): %s", _we)

        # Phase 2: warm the persistent memory store (creates the SQLite DB and
        # the editable profile files). Non-fatal -- memory is always fail-soft.
        try:
            from app.services.memory_service import get_memory
            logger.info("[STARTUP] Persistent memory ready: %s", get_memory().status())
        except Exception as _me:  # noqa: BLE001
            logger.warning("[STARTUP] Memory store init issue (non-fatal): %s", _me)

        _t = time.perf_counter()
        logger.info("[STARTUP] Loading embedding model '%s' on CPU (first run downloads it; usually the slowest step)...", EMBEDDING_MODEL)
        vector_store_service = VectorStoreService()
        logger.info("[STARTUP] 1/5 Embedding model loaded in %.2fs", time.perf_counter() - _t)

        _t = time.perf_counter()
        vector_store_service.create_vector_store()
        logger.info("[STARTUP] 2/5 Vector index built in %.2fs", time.perf_counter() - _t)

        _t = time.perf_counter()
        groq_service = GroqService(vector_store_service)
        realtime_service = RealtimeGroqService(vector_store_service)
        brain_service = BrainService(groq_service=groq_service)
        vision_service = VisionService()
        logger.info("[STARTUP] 3/5 LLM services (Groq + Realtime + Brain + Vision) ready in %.2fs", time.perf_counter() - _t)

        _t = time.perf_counter()
        agent_deps.configure(
            groq_service=groq_service,
            gmail_service=GmailService(),
            calendar_service=CalendarService(),
            drive_service=DriveService(),
        )
        load_all_tools()
        from app.services.agent.tool_registry import registry as _tool_registry
        agent_loop = AgentLoop()
        logger.info("[STARTUP] 4/5 Agent loop + %d tools ready in %.2fs", len(_tool_registry.names()), time.perf_counter() - _t)

        # Phase 4: start Checker + Vision + Skill-maker + Learner (background,
        # fail-soft). PHASE4_ENABLED=False makes JARVIS behave like Phase 3.
        try:
            from app.services.agent.phase4 import get_phase4
            get_phase4().start()
        except Exception as _p4e:  # noqa: BLE001
            logger.warning("[STARTUP] Phase 4 could not start (non-fatal): %s", _p4e)

        # Phase 5: start the Planner/Executor coordinator (reuses Phase 4's bus +
        # checker, fail-soft). PHASE5_ENABLED=False makes JARVIS behave like P4.
        try:
            from app.services.agent.planner import get_phase5
            get_phase5().start()
        except Exception as _p5e:  # noqa: BLE001
            logger.warning("[STARTUP] Phase 5 could not start (non-fatal): %s", _p5e)

        # Phase 6: start the verified-command cache (promote on Checker PASS,
        # evict on FAIL, fail-soft). PHASE6_ENABLED=False makes JARVIS behave
        # exactly like Phase 5 (every command goes through the normal path).
        try:
            from app.services.agent.phase6 import get_phase6
            get_phase6().start()
        except Exception as _p6e:  # noqa: BLE001
            logger.warning("[STARTUP] Phase 6 could not start (non-fatal): %s", _p6e)

        # Phase 7: start the proactive engine (suggest-only by default; reacts
        # to watcher events via the bus, fail-soft). PHASE7_ENABLED=False or
        # PROACTIVE_AUTO_ACT=False keeps JARVIS from ever acting on its own.
        try:
            from app.services.agent.phase7 import get_phase7
            get_phase7().start()
        except Exception as _p7e:  # noqa: BLE001
            logger.warning("[STARTUP] Phase 7 could not start (non-fatal): %s", _p7e)

        # Phase 8: start the user model (facts + aliases + learned habits),
        # aggregate habits from the action log once at boot, then feed those
        # habits to Phase 7 so proactive suggestions are personalized.
        try:
            from app.services.agent.phase8 import get_phase8
            _p8 = get_phase8()
            _p8.start()
            try:
                _p8.aggregate_from_provider()
            except Exception as _p8a:  # noqa: BLE001
                logger.debug("[STARTUP] Phase 8 habit aggregation skipped: %s", _p8a)
            try:
                from app.services.agent.phase7 import get_phase7 as _gp7
                _gp7().set_habit_provider(_p8.habits_for)
            except Exception as _p8w:  # noqa: BLE001
                logger.debug("[STARTUP] Phase 8->7 wiring skipped: %s", _p8w)
        except Exception as _p8e:  # noqa: BLE001
            logger.warning("[STARTUP] Phase 8 could not start (non-fatal): %s", _p8e)

        _t = time.perf_counter()
        chat_service = ChatService(
            groq_service, realtime_service, brain_service,
            vision_service=vision_service,
            agent_loop=agent_loop,
        )
        logger.info("[STARTUP] 5/5 Chat service ready in %.2fs", time.perf_counter() - _t)

        logger.info("=" * 60)
        logger.info(
            "J.A.R.V.I.S ONLINE in %.2fs total  |  %d tools  |  %d Groq keys  |  Gemini: %s  |  Serper: %s",
            time.perf_counter() - _t_start,
            len(_tool_registry.names()),
            len(GROQ_API_KEYS),
            "ON" if llm_providers.gemini_enabled() else "OFF",
            "ON" if SERPER_API_KEY else "OFF",
        )
        logger.info("Frontend: http://localhost:8000/jarvis/   |   API: http://localhost:8000")
        logger.info("=" * 60)

        yield

        logger.info("\nShutting down J.A.R.V.I.S...")
        _tts_pool.shutdown(wait=True)

        try:
            from app.services.watcher import get_watcher
            get_watcher().stop()
        except Exception as _we:  # noqa: BLE001
            logger.debug("[SHUTDOWN] Watcher stop error: %s", _we)

        try:
            from app.services.agent.phase8 import get_phase8
            get_phase8().stop()
        except Exception as _p8e:  # noqa: BLE001
            logger.debug("[SHUTDOWN] Phase 8 stop error: %s", _p8e)

        try:
            from app.services.agent.phase7 import get_phase7
            get_phase7().stop()
        except Exception as _p7e:  # noqa: BLE001
            logger.debug("[SHUTDOWN] Phase 7 stop error: %s", _p7e)

        try:
            from app.services.agent.phase6 import get_phase6
            get_phase6().stop()
        except Exception as _p6e:  # noqa: BLE001
            logger.debug("[SHUTDOWN] Phase 6 stop error: %s", _p6e)

        try:
            from app.services.agent.planner import get_phase5
            get_phase5().stop()
        except Exception as _p5e:  # noqa: BLE001
            logger.debug("[SHUTDOWN] Phase 5 stop error: %s", _p5e)

        try:
            from app.services.agent.phase4 import get_phase4
            get_phase4().stop()
        except Exception as _p4e:  # noqa: BLE001
            logger.debug("[SHUTDOWN] Phase 4 stop error: %s", _p4e)

        if chat_service:
            for session_id in list(chat_service.sessions.keys()):
                chat_service.save_chat_session(session_id)

        logger.info("All sessions saved. Goodbye!")

    except Exception as e:
        logger.error(f"Fatal error during startup: {e}", exc_info=True)
        raise

app = FastAPI(
    title="J.A.R.V.I.S API",
    description="Just A Rather Very Intelligent System",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Endpoints the UI polls on a timer (the Control Center hits these every ~2s).
# Logging each hit floods the console with useless noise, so skip them. The
# slow ones still log: see the SLOW_REQUEST_SECONDS guard below.
_QUIET_REQUEST_PATHS = {"/api/watcher/state", "/api/dashboard/state"}
# Even a quiet endpoint gets logged if it is unusually slow (helps catch real
# problems without the per-2s spam).
_SLOW_REQUEST_SECONDS = 1.0

class TimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        t0 = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - t0
        path = request.url.path
        noisy = path in _QUIET_REQUEST_PATHS
        if not noisy:
            logger.info("[REQUEST] %s %s -> %s (%.3fs)", request.method, path, response.status_code, elapsed)
        elif response.status_code >= 400 or elapsed >= _SLOW_REQUEST_SECONDS:
            # Polling endpoint, but something is wrong (error or slow) -- worth a line.
            logger.warning("[REQUEST] %s %s -> %s (%.3fs)", request.method, path, response.status_code, elapsed)
        return response

app.add_middleware(TimingMiddleware)

@app.get("/api")

async def api_info():
    return {
        "message": "J.A.R.V.I.S API",
        "endpoints": {
            "/chat": "General chat (non-streaming)",
            "/chat/stream": "General chat (streaming chunks)",
            "/chat/realtime": "Realtime chat (non-streaming)",
            "/chat/realtime/stream": "Realtime chat (streaming chunks)",
            "/chat/jarvis/stream": "Jarvis unified route (two-stage brain: classify → route �� execute/stream)",
            "/chat/history/{session_id}": "Get chat history",
            "/health": "System health check",
            "/tts": "Text-to-speech (POST text, returns streamed MP3)"
        }
    }

@app.get("/health")

async def health():

    try:
        return {
            "status": "healthy",
            "vector_store": vector_store_service is not None,
            "groq_service": groq_service is not None,
            "realtime_service": realtime_service is not None,
            "brain_service": brain_service is not None,
            "agent_loop": agent_loop is not None,
            "vision_service": vision_service is not None,
            "chat_service": chat_service is not None
        }
    
    except Exception as e:
        logger.warning("[API /health] Error: %s", e)
        return {"status": "degraded", "error": str(e)}

@app.post("/chat", response_model=ChatResponse)

async def chat(request: ChatRequest):

    if not chat_service:
        raise HTTPException(status_code=503, detail="Chat service not initialized")

    logger.info("[API /chat] Incoming | session_id=%s | message_len=%d | message=%.100s",
                request.session_id or "new", len(request.message), request.message)
    
    try:
        session_id = chat_service.get_or_create_session(request.session_id)
        response_text = chat_service.process_message(session_id, request.message)
        chat_service.save_chat_session(session_id)
        logger.info("[API /chat] Done | session_id=%s | response_len=%d", session_id[:12], len(response_text))
        return ChatResponse(response=response_text, session_id=session_id)

    except ValueError as e:
        logger.warning("[API /chat] Invalid session_id: %s", e)
        raise HTTPException(status_code=400, detail=str(e))

    except AllGroqApisFailedError as e:
        logger.error("[API /chat] All Groq APIs failed: %s", e)
        raise HTTPException(status_code=503, detail=str(e))

    except Exception as e:

        if _is_rate_limit_error(e):
            logger.warning("[API /chat] Rate limit hit: %s", e)
            raise HTTPException(status_code=429, detail=RATE_LIMIT_MESSAGE)
        
        logger.error("[API /chat] Error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error processing chat: {str(e)}")

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

def _tts_cache_key(text: str):
    """Return (cleaned_text, cache_path) for a given TTS input string."""
    cleaned = text.strip().lower()
    cleaned = re.sub(r"[^\w\s\u0900-\u097f]", "", cleaned)  # English + Hindi Unicode
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return None, None
    hash_key = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()
    return cleaned, VOICE_CACHE_DIR / f"{hash_key}.mp3"


def _generate_tts_sync(text: str, voice: str, rate: str, activity_q=None) -> bytes:
    """Called from ThreadPoolExecutor. Checks local cache first; synthesizes online on miss.
    Emits tts_cache_hit / tts_cache_miss_saved activity events into activity_q if provided."""
    _, cache_path = _tts_cache_key(text)

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

_tts_pool = ThreadPoolExecutor(max_workers=4)

def _stream_generator(session_id: str, chunk_iter, is_realtime: bool, tts_enabled: bool = False):
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
            _tts_pool.submit(_generate_tts_sync, text, TTS_VOICE, TTS_RATE, tts_activity_q),
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

@app.post("/chat/stream")

async def chat_stream(request: ChatRequest):

    if not chat_service:
        raise HTTPException(status_code=503, detail="Chat service not initialized")
    
    logger.info("[API /chat/stream] Incoming | session_id=%s | message_len=%d | message=%.100s",
                request.session_id or "new", len(request.message), request.message)
    
    try:
        session_id = chat_service.get_or_create_session(request.session_id)

        chunk_iter = chat_service.process_message_stream(session_id, request.message)
        return StreamingResponse(
            _stream_generator(session_id, chunk_iter, is_realtime=False, tts_enabled=request.tts),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    except AllGroqApisFailedError as e:
        raise HTTPException(status_code=503, detail=str(e))
    
    except Exception as e:
        if _is_rate_limit_error(e):
            raise HTTPException(status_code=429, detail=RATE_LIMIT_MESSAGE)
        
        logger.error("[API /chat/stream] Error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/chat/realtime", response_model=ChatResponse)

async def chat_realtime(request: ChatRequest):

    if not chat_service:
        raise HTTPException(status_code=503, detail="Chat service not initialized")
    
    if not realtime_service:
        raise HTTPException(status_code=503, detail="Realtime service not initialized")

    logger.info("[API /chat/realtime] Incoming | session_id=%s | message_len=%d | message=%.100s",
                request.session_id or "new", len(request.message), request.message)
    
    try:
        session_id = chat_service.get_or_create_session(request.session_id)
        response_text = chat_service.process_realtime_message(session_id, request.message)
        chat_service.save_chat_session(session_id)
        logger.info("[API /chat/realtime] Done | session_id=%s | response_len=%d", session_id[:12], len(response_text))
        return ChatResponse(response=response_text, session_id=session_id)
    
    except ValueError as e:
        logger.warning("[API /chat/realtime] Invalid session_id: %s", e)
        raise HTTPException(status_code=400, detail=str(e))
    
    except AllGroqApisFailedError as e:
        logger.error("[API /chat/realtime] All Groq APIs failed: %s", e)
        raise HTTPException(status_code=503, detail=str(e))
    
    except Exception as e:

        if _is_rate_limit_error(e):
            logger.warning("[API /chat/realtime] Rate limit hit: %s", e)
            raise HTTPException(status_code=429, detail=RATE_LIMIT_MESSAGE)
        
        logger.error("[API /chat/realtime] Error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error processing chat: {str(e)}")

@app.post("/chat/realtime/stream")

async def chat_realtime_stream(request: ChatRequest):

    if not chat_service or not realtime_service:
        raise HTTPException(status_code=503, detail="Service not initialized")
    
    logger.info("[API /chat/realtime/stream] Incoming | session_id=%s | message_len=%d | message=%.100s",
                request.session_id or "new", len(request.message), request.message)
    
    try:
        session_id = chat_service.get_or_create_session(request.session_id)
        chunk_iter = chat_service.process_realtime_message_stream(session_id, request.message)
        return StreamingResponse(
            _stream_generator(session_id, chunk_iter, is_realtime=True, tts_enabled=request.tts),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    except AllGroqApisFailedError as e:
        raise HTTPException(status_code=503, detail=str(e))
    
    except Exception as e:
        if _is_rate_limit_error(e):
            raise HTTPException(status_code=429, detail=RATE_LIMIT_MESSAGE)
        
        logger.error("[API /chat/realtime/stream] Error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/chat/jarvis/stream")

async def chat_jarvis_stream(request: ChatRequest):
    if not chat_service:
        raise HTTPException(status_code=503, detail="Service not initialized")
    
    logger.info("[API /chat/jarvis/stream] Incoming | session_id=%s | message_len=%d | img=%s | message=%.100s",
                request.session_id or "new", len(request.message), "yes" if request.imgbase64 else "no", request.message)
    
    try:
        session_id = chat_service.get_or_create_session(request.session_id)
        chunk_iter = chat_service.process_jarvis_message_stream(
            session_id, request.message, imgbase64=request.imgbase64
        )

        return StreamingResponse(
            _stream_generator(session_id, chunk_iter, is_realtime=True, tts_enabled=request.tts),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    except AllGroqApisFailedError as e:
        raise HTTPException(status_code=503, detail=str(e))
    
    except Exception as e:
        if _is_rate_limit_error(e):
            raise HTTPException(status_code=429, detail=RATE_LIMIT_MESSAGE)
        
        logger.error("[API /chat/jarvis/stream] Error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/startup-brief/stream")

async def get_startup_brief_stream(session_id: str = None):
    if not chat_service:
        raise HTTPException(status_code=503, detail="Service not initialized")
    
    logger.info("[API /api/startup-brief/stream] Incoming | session_id=%s", session_id or "new")
    
    try:
        sid = chat_service.get_or_create_session(session_id)
        chunk_iter = chat_service.process_startup_brief_stream(sid)

        return StreamingResponse(
            _stream_generator(sid, chunk_iter, is_realtime=True, tts_enabled=True),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    
    except Exception as e:
        if _is_rate_limit_error(e):
            raise HTTPException(status_code=429, detail=RATE_LIMIT_MESSAGE)
        logger.error("[API /api/startup-brief/stream] Error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/chat/history/{session_id}")

async def get_chat_history(session_id: str):
    if not chat_service:
        raise HTTPException(status_code=503, detail="Chat service not initialized")
    
    if not chat_service.validate_session_id(session_id):
        raise HTTPException(status_code=400, detail="Invalid session_id format")

    try:
        messages = chat_service.get_chat_history(session_id)
        return {
            "session_id": session_id,
            "messages": [{"role": msg.role, "content": msg.content} for msg in messages]
        }
    
    except Exception as e:
        logger.error(f"Error retrieving history: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error retrieving history: {str(e)}")

@app.post("/tts")
async def text_to_speech(request: TTSRequest):
    """Standalone TTS endpoint. Checks local cache first; synthesizes + saves on miss."""
    from fastapi.responses import FileResponse

    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")

    # Compute cache key — always defined, never conditional
    _, cache_path = _tts_cache_key(text)

    # --- Cache Hit: serve the file directly from SSD ---
    if cache_path is not None and cache_path.exists():
        logger.info("[TTS-API] Cache hit  '%s'", text[:50])
        return FileResponse(
            path=str(cache_path),
            media_type="audio/mpeg",
            headers={"Cache-Control": "no-cache"},
        )

    # --- Cache Miss: synthesize online, stream to client, save to disk ---
    logger.info("[TTS-API] Cache miss '%s' -> synthesizing online...", text[:50])

    async def generate_and_cache():
        try:
            communicate = edge_tts.Communicate(text=text, voice=TTS_VOICE, rate=TTS_RATE)
            parts = []
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    parts.append(chunk["data"])
                    yield chunk["data"]
            # After streaming is complete, persist to disk for future cache hits
            if parts and cache_path is not None:
                try:
                    VOICE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    cache_path.write_bytes(b"".join(parts))
                    logger.info("[TTS-API] Saved to cache '%s'", text[:50])
                except Exception as save_exc:
                    logger.warning("[TTS-API] Cache save failed: %s", save_exc)
        except Exception as exc:
            logger.error("[TTS-API] Synthesis error: %s", exc)

    return StreamingResponse(
        generate_and_cache(),
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-cache"},
    )

@app.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    """Transcribe audio using Groq Whisper. Fallback for Web Speech API."""
    if not GROQ_API_KEYS:
        raise HTTPException(status_code=503, detail="No Groq API keys configured")

    audio_bytes = await file.read()
    if not audio_bytes or len(audio_bytes) < 100:
        raise HTTPException(status_code=400, detail="Audio file is empty or too small")

    logger.info("[TRANSCRIBE] Received audio: %d bytes, type=%s", len(audio_bytes), file.content_type or "unknown")

    import io
    last_err = None
    for i, key in enumerate(GROQ_API_KEYS):
        try:
            from groq import Groq
            client = Groq(api_key=key)
            # Wrap bytes in a file-like tuple: (filename, file_obj, content_type)
            audio_file = ("audio.webm", io.BytesIO(audio_bytes), file.content_type or "audio/webm")
            transcription = client.audio.transcriptions.create(
                model="whisper-large-v3-turbo",
                file=audio_file,
                language="en",
            )
            text = (transcription.text or "").strip()
            logger.info("[TRANSCRIBE] Success (key %d): '%s'", i, text[:100])
            return {"text": text}
        except Exception as e:
            last_err = e
            err_str = str(e).lower()
            if "429" in str(e) or "rate limit" in err_str:
                logger.warning("[TRANSCRIBE] Key %d rate limited, trying next...", i)
                continue
            logger.error("[TRANSCRIBE] Key %d error: %s", i, e)
            continue

    logger.error("[TRANSCRIBE] All keys failed. Last error: %s", last_err)
    raise HTTPException(status_code=503, detail=f"Transcription failed: {last_err}")

@app.get("/api/key-monitor")
async def api_key_monitor_snapshot():
    return get_api_key_monitor().snapshot()

# --------------------------------------------------------------------------- #
# Watcher dashboard (Phase 1 monitor: live system-state view)
# --------------------------------------------------------------------------- #
_WATCHER_DASH_FILE = Path(__file__).resolve().parent / "static" / "watcher_dashboard.html"

@app.get("/api/watcher/state")
async def api_watcher_state():
    """Live snapshot from the background system-state watcher daemon."""
    from app.services.watcher import get_watcher
    w = get_watcher()
    th = getattr(w, "_thread", None)
    return {
        "running": bool(th and th.is_alive()),
        "interval": getattr(w, "_interval", None),
        "timestamp": time.time(),
        **w.get_state(),
    }

_CONTROL_DASH_FILE = Path(__file__).resolve().parent / "static" / "dashboard.html"


# --------------------------------------------------------------------------- #
# Command Tester (bulk live testing tool)
# --------------------------------------------------------------------------- #
# Runs commands ONE-BY-ONE through the SAME pipeline as /chat/jarvis/stream and
# streams an HONEST per-command verdict (watcher/tool-result/soft-LLM-judge --
# never trusting the assistant's narration). Fully isolated from the core agent.
@app.post("/api/test-session/run")
async def api_test_session_run(request: Request):
    """Run newline-separated (or array) commands LIVE, one-by-one, and stream
    per-command verdicts as SSE. Reuses the exact /chat/jarvis/stream path."""
    if chat_service is None:
        raise HTTPException(status_code=503, detail="Chat service not ready")
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    raw = body.get("commands")
    if isinstance(raw, str):
        commands = list(raw.splitlines())
    elif isinstance(raw, list):
        commands = [str(x) for x in raw]
    else:
        commands = []
    on_risky = str(body.get("on_risky") or "skip")
    on_fail = str(body.get("on_fail") or "continue")
    judge = bool(body.get("judge", True))

    from app.services.testing import get_command_tester
    tester = get_command_tester()
    gen = tester.run_stream(
        chat_service, commands,
        on_risky=on_risky, on_fail=on_fail, judge=judge,
    )
    return StreamingResponse(
        gen, media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/test-session/{session_id}/logs")
async def api_test_session_logs(session_id: str):
    """Download the complete, terminal-identical log for one test session."""
    from app.services.testing import get_command_tester
    tester = get_command_tester()
    text = tester.get_log_text(session_id)
    if text is None:
        raise HTTPException(status_code=404, detail="Unknown test session")
    label = tester.get_log_label(session_id)
    return Response(
        content=text, media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="%s"' % label},
    )


@app.get("/api/dashboard/state")
async def api_dashboard_state():
    """Aggregated snapshot for the unified Control Center (/dashboard):
    system health + watcher (P1) + checker/learner/skills/bus (P4) + providers."""
    out = {"timestamp": time.time(), "system": {}, "watcher": {}, "phase4": {}, "keys": {}}

    # --- Watcher (Phase 1) ---
    try:
        from app.services.watcher import get_watcher
        w = get_watcher()
        th = getattr(w, "_thread", None)
        out["watcher"] = {
            "running": bool(th and th.is_alive()),
            "interval": getattr(w, "_interval", None),
            **w.get_state(),
        }
    except Exception as e:  # noqa: BLE001
        out["watcher"] = {"running": False, "error": str(e)}

    # --- Phase 4 (bus / checker / learner / skills) ---
    try:
        from app.services.agent.phase4 import get_phase4
        coord = get_phase4()
        st = coord.stats()
        p4 = {
            "health": coord.health(),
            "bus": st.get("bus", {}),
            "skills": st.get("skills", {}),
            "recent": coord.recent_activity(30),
            "skills_list": [],
            "learner_notes": [],
            "learner_max_retries": None,
            "learner_research": False,
        }
        try:
            if getattr(coord, "store", None) is not None:
                p4["skills_list"] = coord.store.list_skills(limit=25, only_active=False)
        except Exception:  # noqa: BLE001
            pass
        try:
            if getattr(coord, "learner", None) is not None:
                p4["learner_notes"] = coord.learner.recent_notes()
                p4["learner_max_retries"] = coord.learner.max_retries
                p4["learner_research"] = coord.learner.research_enabled
        except Exception:  # noqa: BLE001
            pass
        try:
            from config import SKILL_MIN_REPEATS
            if isinstance(p4["skills"], dict):
                p4["skills"]["min_repeats"] = SKILL_MIN_REPEATS
        except Exception:  # noqa: BLE001
            pass
        out["phase4"] = p4
    except Exception as e:  # noqa: BLE001
        out["phase4"] = {"health": {"enabled": False, "started": False}, "error": str(e)}

    # --- Phase 5 (planner / multi-step executor / UIA) ---
    try:
        from app.services.agent.planner import get_phase5
        p5c = get_phase5()
        out["phase5"] = {
            "health": p5c.health(),
            "stats": p5c.stats(),
            "last_plan": p5c.last_plan(),
            "recent": p5c.recent_activity(15),
        }
    except Exception as e:  # noqa: BLE001
        out["phase5"] = {"health": {"enabled": False, "started": False}, "error": str(e)}

    # --- Phase 6 (verified-command cache) ---
    try:
        from app.services.agent.phase6 import get_phase6
        p6c = get_phase6()
        out["phase6"] = {
            "health": p6c.health(),
            "stats": p6c.stats(),
            "recent": p6c.recent_activity(15),
            "entries": p6c.list_entries(limit=25),
        }
    except Exception as e:  # noqa: BLE001
        out["phase6"] = {"health": {"enabled": False, "started": False}, "error": str(e)}

    # --- Phase 7 (proactive engine) ---
    try:
        from app.services.agent.phase7 import get_phase7
        p7c = get_phase7()
        out["phase7"] = {
            "health": p7c.health(),
            "stats": p7c.stats(),
            "pending": p7c.get_pending(10),
            "recent": p7c.recent_activity(15),
        }
    except Exception as e:  # noqa: BLE001
        out["phase7"] = {"health": {"enabled": False, "started": False}, "error": str(e)}

    # --- Phase 8 (user model / personalization) ---
    try:
        from app.services.agent.phase8 import get_phase8
        p8c = get_phase8()
        out["phase8"] = {
            "health": p8c.health(),
            "stats": p8c.stats(),
            "knowledge": p8c.knowledge_summary(),
        }
    except Exception as e:  # noqa: BLE001
        out["phase8"] = {"health": {"enabled": False, "started": False}, "error": str(e)}

    # --- API keys / providers ---
    try:
        out["keys"] = get_api_key_monitor().snapshot()
    except Exception as e:  # noqa: BLE001
        out["keys"] = {"error": str(e)}

    # --- System services ---
    sysinfo = {"agent_loop": agent_loop is not None, "vision": vision_service is not None}
    try:
        from app.services.memory_service import get_memory
        sysinfo["memory"] = get_memory() is not None
    except Exception:  # noqa: BLE001
        sysinfo["memory"] = False
    out["system"] = sysinfo
    return out

# --------------------------------------------------------------------------- #
# Phase 7 -- Proactive suggestions (suggest-only; user stays in control)
# --------------------------------------------------------------------------- #
@app.get("/api/proactive/pending")
async def api_proactive_pending():
    """List the proactive suggestions awaiting the user's decision."""
    from app.services.agent.phase7 import get_phase7
    p7 = get_phase7()
    return {"pending": p7.get_pending(20), "stats": p7.stats()}


@app.post("/api/proactive/accept")
async def api_proactive_accept(request: Request):
    """Accept a suggestion. Returns the command to run; if a chat service and
    session are available, runs it through the normal JARVIS pipeline."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    sid = str(body.get("id") or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="Missing suggestion id")
    from app.services.agent.phase7 import get_phase7
    sug = get_phase7().accept(sid)
    if not sug:
        raise HTTPException(status_code=404, detail="Unknown or already-resolved suggestion")
    return {"ok": True, "suggestion": sug}


@app.post("/api/proactive/dismiss")
async def api_proactive_dismiss(request: Request):
    """Dismiss a suggestion without running it."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    sid = str(body.get("id") or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="Missing suggestion id")
    from app.services.agent.phase7 import get_phase7
    ok = get_phase7().dismiss(sid)
    return {"ok": bool(ok)}


@app.post("/api/proactive/consent")
async def api_proactive_consent(request: Request):
    """Set consent mode (ask/allow/deny) for a proactive action."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    action = str(body.get("action") or "").strip()
    mode = str(body.get("mode") or "").strip()
    from app.services.agent.phase7 import get_phase7
    ok = get_phase7().set_consent(action, mode)
    if not ok:
        raise HTTPException(status_code=400, detail="Invalid action or mode")
    return {"ok": True, "action": action.lower(), "mode": mode}


# --------------------------------------------------------------------------- #
# Phase 8 -- "What JARVIS knows about you" + forget controls (privacy)
# --------------------------------------------------------------------------- #
@app.get("/api/usermodel/knowledge")
async def api_usermodel_knowledge():
    """Everything JARVIS has learned about the user (facts/aliases/habits)."""
    from app.services.agent.phase8 import get_phase8
    p8 = get_phase8()
    return {"knowledge": p8.knowledge_summary(), "stats": p8.stats()}


@app.post("/api/usermodel/forget")
async def api_usermodel_forget(request: Request):
    """Forget a fact, alias, habit, or everything (scope=all)."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    scope = str(body.get("scope") or "").strip().lower()
    from app.services.agent.phase8 import get_phase8
    p8 = get_phase8()
    if scope == "all":
        return {"ok": p8.forget_all()}
    if scope == "fact":
        return {"ok": p8.forget_fact(str(body.get("key") or ""))}
    if scope == "alias":
        return {"ok": p8.forget_alias(str(body.get("alias") or ""))}
    if scope == "habit":
        return {"ok": p8.forget_habit(str(body.get("context") or ""), body.get("action"))}
    raise HTTPException(status_code=400, detail="scope must be one of: fact, alias, habit, all")


@app.get("/dashboard")
async def control_center():
    """Unified Control Center: app + watcher + checker + learner + skills."""
    try:
        html = _CONTROL_DASH_FILE.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        html = f"<h1>Control Center unavailable</h1><p>{e}</p>"
    return HTMLResponse(html)

@app.get("/watcher")
async def watcher_dashboard():
    """Serve the focused watcher (Phase 1) live dashboard."""
    try:
        html = _WATCHER_DASH_FILE.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        html = f"<h1>Watcher dashboard unavailable</h1><p>{e}</p>"
    return HTMLResponse(html)

_frontend_dir = Path(__file__).resolve().parent.parent / "frontend"

if _frontend_dir.exists():
    # Canonical: serve the JARVIS app UI directly at /jarvis (no redirect to /app).
    app.mount("/jarvis", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")
    # Keep /app working too: the frontend JS hardcodes a few /app/* asset paths
    # (e.g. /app/audio/*, /app/api-monitor.html); removing it would break them.
    app.mount("/app", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend_app")

@app.get("/")
async def root_redirect():
    return RedirectResponse(url="/jarvis/", status_code=302)

def run():
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info"
    )

if __name__ == "__main__":
    run()
