"""Chat routes: /chat, /chat/stream, /chat/realtime, /chat/realtime/stream,
/chat/jarvis/stream, and the conversation-history collection routes
(GET/PATCH/DELETE /chat/history[/{session_id}])."""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

import app.core.state as state
from app.core.helpers import RATE_LIMIT_MESSAGE, is_rate_limit_error
from app.core.streaming import stream_generator
from app.models import (
    ChatRequest,
    ChatResponse,
    ConversationDetail,
    ConversationList,
    ConversationRenameRequest,
    ConversationSummary,
)
from config import HISTORY_MAX_PAGE_SIZE, HISTORY_PAGE_SIZE, HISTORY_SEARCH_MAX_CHARS
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


def _require_chat_service():
    if not state.chat_service:
        raise HTTPException(status_code=503, detail="Chat service not initialized")
    return state.chat_service


def _require_valid_session_id(service, session_id: str) -> str:
    if not service.validate_session_id(session_id):
        raise HTTPException(status_code=400, detail="Invalid session_id format")
    return session_id


@router.get("/chat/history", response_model=ConversationList)
async def list_chat_history(
    query: str = Query("", max_length=HISTORY_SEARCH_MAX_CHARS),
    limit: int = Query(HISTORY_PAGE_SIZE, ge=1, le=HISTORY_MAX_PAGE_SIZE),
    cursor: Optional[str] = Query(None, max_length=200),
):
    """Newest-first conversation summaries. Previews only -- never full transcripts."""
    service = _require_chat_service()
    try:
        return service.list_conversations(query=query, limit=limit, cursor=cursor)
    except Exception as e:
        logger.error("[API /chat/history] List failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Could not load conversation history")


@router.get("/chat/history/{session_id}", response_model=ConversationDetail)
async def get_chat_history(session_id: str):
    service = _require_chat_service()
    _require_valid_session_id(service, session_id)

    try:
        conversation = service.get_conversation(session_id)
    except Exception as e:
        logger.error("[API /chat/history/{id}] Read failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Could not load conversation")

    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@router.patch("/chat/history/{session_id}", response_model=ConversationSummary)
async def rename_chat_history(session_id: str, request: ConversationRenameRequest):
    service = _require_chat_service()
    _require_valid_session_id(service, session_id)

    try:
        summary = service.rename_conversation(session_id, request.title)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("[API /chat/history/{id}] Rename failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Could not rename conversation")

    if summary is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return summary


@router.delete("/chat/history/{session_id}")
async def delete_chat_history(session_id: str):
    """Permanently deletes the conversation file. Not recoverable."""
    service = _require_chat_service()
    _require_valid_session_id(service, session_id)

    try:
        deleted = service.delete_conversation(session_id)
    except Exception as e:
        logger.error("[API /chat/history/{id}] Delete failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Could not delete conversation")

    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"deleted": True, "session_id": session_id}
