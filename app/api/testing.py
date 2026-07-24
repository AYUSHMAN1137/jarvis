"""Command testing routes: /api/test-session/run, /api/test-session/{session_id}/logs."""

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, Response
from starlette.requests import Request

import app.core.state as state

logger = logging.getLogger("J.A.R.V.I.S")

router = APIRouter()


@router.post("/api/test-session/run")
async def api_test_session_run(request: Request):
    """Run newline-separated (or array) commands LIVE, one-by-one, and stream
    per-command verdicts as SSE. Reuses the exact /chat/jarvis/stream path."""
    if state.chat_service is None:
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
        state.chat_service, commands,
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


@router.get("/api/test-session/{session_id}/logs")
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
