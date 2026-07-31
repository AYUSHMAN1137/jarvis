"""System routes: /api info, /health, /api/key-monitor, /api/startup-brief/stream."""

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse

import app.core.state as state
from app.core.helpers import RATE_LIMIT_MESSAGE, is_rate_limit_error
from app.core.streaming import stream_generator
from app.services.api_key_monitor import get_api_key_monitor

logger = logging.getLogger("J.A.R.V.I.S")

router = APIRouter()

# Server-session flag: the startup brief is delivered at most once per server
# lifetime.  Resets when the server restarts (module re-imported), persists
# across browser page reloads (Ctrl+R).
_startup_brief_delivered = False


@router.get("/api")
async def api_info():
    return {
        "message": "J.A.R.V.I.S API",
        "endpoints": {
            "/chat": "General chat (non-streaming)",
            "/chat/stream": "General chat (streaming chunks)",
            "/chat/realtime": "Realtime chat (non-streaming)",
            "/chat/realtime/stream": "Realtime chat (streaming chunks)",
            "/chat/jarvis/stream": "Jarvis unified route (two-stage brain: classify → route → execute/stream)",
            "/chat/history/{session_id}": "Get chat history",
            "/health": "System health check",
            "/tts": "Text-to-speech (POST text, returns streamed MP3)"
        }
    }


@router.get("/health")
async def health():
    try:
        return {
            "status": "healthy",
            "vector_store": state.vector_store_service is not None,
            "groq_service": state.groq_service is not None,
            "realtime_service": state.realtime_service is not None,
            "resolver": state.resolver is not None,
            "agent_loop": state.agent_loop is not None,
            "vision_service": state.vision_service is not None,
            "chat_service": state.chat_service is not None
        }
    except Exception as e:
        logger.warning("[API /health] Error: %s", e)
        return {"status": "degraded", "error": str(e)}


@router.get("/api/key-monitor")
async def api_key_monitor_snapshot():
    return get_api_key_monitor().snapshot()


@router.get("/api/startup-brief/stream")
async def get_startup_brief_stream(session_id: str = None):
    """`session_id` is accepted for backwards compatibility but deliberately
    ignored -- see the comment on the get_or_create_session call below."""
    global _startup_brief_delivered

    # Already delivered this server session — don't repeat on page refresh
    if _startup_brief_delivered:
        logger.info("[API /api/startup-brief/stream] Already delivered this session, returning 204")
        return Response(status_code=204)

    if not state.chat_service:
        raise HTTPException(status_code=503, detail="Service not initialized")

    logger.info("[API /api/startup-brief/stream] Incoming | session_id=%s", session_id or "new")

    # Mark as delivered BEFORE streaming so concurrent refreshes don't race
    _startup_brief_delivered = True

    try:
        # The brief always gets its own throwaway session. It is marked transient
        # (never written to disk), so reusing a caller-supplied session_id would
        # silently stop that real conversation from being saved.
        sid = state.chat_service.get_or_create_session(None)
        chunk_iter = state.chat_service.process_startup_brief_stream(sid)

        return StreamingResponse(
            stream_generator(sid, chunk_iter, is_realtime=True, tts_enabled=True),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    except Exception as e:
        if is_rate_limit_error(e):
            raise HTTPException(status_code=429, detail=RATE_LIMIT_MESSAGE)
        logger.error("[API /api/startup-brief/stream] Error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
