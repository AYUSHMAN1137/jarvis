"""Chat routes: /chat, /chat/stream, /chat/realtime, /chat/realtime/stream,
/chat/jarvis/stream, /chat/history/{session_id}."""

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

import app.core.state as state
from app.core.helpers import RATE_LIMIT_MESSAGE, is_rate_limit_error
from app.core.streaming import stream_generator
from app.models import ChatRequest, ChatResponse
from app.services.groq_service import AllGroqApisFailedError

logger = logging.getLogger("J.A.R.V.I.S")

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    if not state.chat_service:
        raise HTTPException(status_code=503, detail="Chat service not initialized")

    logger.info("[API /chat] Incoming | session_id=%s | message_len=%d | message=%.100s",
                request.session_id or "new", len(request.message), request.message)

    try:
        session_id = state.chat_service.get_or_create_session(request.session_id)
        response_text = state.chat_service.process_message(session_id, request.message)
        state.chat_service.save_chat_session(session_id)
        logger.info("[API /chat] Done | session_id=%s | response_len=%d", session_id[:12], len(response_text))
        return ChatResponse(response=response_text, session_id=session_id)

    except ValueError as e:
        logger.warning("[API /chat] Invalid session_id: %s", e)
        raise HTTPException(status_code=400, detail=str(e))

    except AllGroqApisFailedError as e:
        logger.error("[API /chat] All Groq APIs failed: %s", e)
        raise HTTPException(status_code=503, detail=str(e))

    except Exception as e:
        if is_rate_limit_error(e):
            logger.warning("[API /chat] Rate limit hit: %s", e)
            raise HTTPException(status_code=429, detail=RATE_LIMIT_MESSAGE)
        logger.error("[API /chat] Error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error processing chat: {str(e)}")


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    if not state.chat_service:
        raise HTTPException(status_code=503, detail="Chat service not initialized")

    logger.info("[API /chat/stream] Incoming | session_id=%s | message_len=%d | message=%.100s",
                request.session_id or "new", len(request.message), request.message)

    try:
        session_id = state.chat_service.get_or_create_session(request.session_id)
        chunk_iter = state.chat_service.process_message_stream(session_id, request.message)
        return StreamingResponse(
            stream_generator(session_id, chunk_iter, is_realtime=False, tts_enabled=request.tts),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except AllGroqApisFailedError as e:
        raise HTTPException(status_code=503, detail=str(e))

    except Exception as e:
        if is_rate_limit_error(e):
            raise HTTPException(status_code=429, detail=RATE_LIMIT_MESSAGE)
        logger.error("[API /chat/stream] Error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat/realtime", response_model=ChatResponse)
async def chat_realtime(request: ChatRequest):
    if not state.chat_service:
        raise HTTPException(status_code=503, detail="Chat service not initialized")

    if not state.realtime_service:
        raise HTTPException(status_code=503, detail="Realtime service not initialized")

    logger.info("[API /chat/realtime] Incoming | session_id=%s | message_len=%d | message=%.100s",
                request.session_id or "new", len(request.message), request.message)

    try:
        session_id = state.chat_service.get_or_create_session(request.session_id)
        response_text = state.chat_service.process_realtime_message(session_id, request.message)
        state.chat_service.save_chat_session(session_id)
        logger.info("[API /chat/realtime] Done | session_id=%s | response_len=%d", session_id[:12], len(response_text))
        return ChatResponse(response=response_text, session_id=session_id)

    except ValueError as e:
        logger.warning("[API /chat/realtime] Invalid session_id: %s", e)
        raise HTTPException(status_code=400, detail=str(e))

    except AllGroqApisFailedError as e:
        logger.error("[API /chat/realtime] All Groq APIs failed: %s", e)
        raise HTTPException(status_code=503, detail=str(e))

    except Exception as e:
        if is_rate_limit_error(e):
            logger.warning("[API /chat/realtime] Rate limit hit: %s", e)
            raise HTTPException(status_code=429, detail=RATE_LIMIT_MESSAGE)
        logger.error("[API /chat/realtime] Error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error processing chat: {str(e)}")


@router.post("/chat/realtime/stream")
async def chat_realtime_stream(request: ChatRequest):
    if not state.chat_service or not state.realtime_service:
        raise HTTPException(status_code=503, detail="Service not initialized")

    logger.info("[API /chat/realtime/stream] Incoming | session_id=%s | message_len=%d | message=%.100s",
                request.session_id or "new", len(request.message), request.message)

    try:
        session_id = state.chat_service.get_or_create_session(request.session_id)
        chunk_iter = state.chat_service.process_realtime_message_stream(session_id, request.message)
        return StreamingResponse(
            stream_generator(session_id, chunk_iter, is_realtime=True, tts_enabled=request.tts),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except AllGroqApisFailedError as e:
        raise HTTPException(status_code=503, detail=str(e))

    except Exception as e:
        if is_rate_limit_error(e):
            raise HTTPException(status_code=429, detail=RATE_LIMIT_MESSAGE)
        logger.error("[API /chat/realtime/stream] Error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat/jarvis/stream")
async def chat_jarvis_stream(request: ChatRequest):
    if not state.chat_service:
        raise HTTPException(status_code=503, detail="Service not initialized")

    logger.info("[API /chat/jarvis/stream] Incoming | session_id=%s | message_len=%d | img=%s | message=%.100s",
                request.session_id or "new", len(request.message), "yes" if request.imgbase64 else "no", request.message)

    try:
        session_id = state.chat_service.get_or_create_session(request.session_id)
        chunk_iter = state.chat_service.process_jarvis_message_stream(
            session_id, request.message, imgbase64=request.imgbase64
        )

        return StreamingResponse(
            stream_generator(session_id, chunk_iter, is_realtime=True, tts_enabled=request.tts),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except AllGroqApisFailedError as e:
        raise HTTPException(status_code=503, detail=str(e))

    except Exception as e:
        if is_rate_limit_error(e):
            raise HTTPException(status_code=429, detail=RATE_LIMIT_MESSAGE)
        logger.error("[API /chat/jarvis/stream] Error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/chat/history/{session_id}")
async def get_chat_history(session_id: str):
    if not state.chat_service:
        raise HTTPException(status_code=503, detail="Chat service not initialized")

    if not state.chat_service.validate_session_id(session_id):
        raise HTTPException(status_code=400, detail="Invalid session_id format")

    try:
        messages = state.chat_service.get_chat_history(session_id)
        return {
            "session_id": session_id,
            "messages": [{"role": msg.role, "content": msg.content} for msg in messages]
        }

    except Exception as e:
        logger.error(f"Error retrieving history: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error retrieving history: {str(e)}")
