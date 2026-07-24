"""Proactive suggestion routes: /api/proactive/pending, accept, dismiss, consent."""

import logging

from fastapi import APIRouter, HTTPException
from starlette.requests import Request

logger = logging.getLogger("J.A.R.V.I.S")

router = APIRouter()


@router.get("/api/proactive/pending")
async def api_proactive_pending():
    """List the proactive suggestions awaiting the user's decision."""
    from app.services.agent.proactive import get_phase7
    p7 = get_phase7()
    return {"pending": p7.get_pending(20), "stats": p7.stats()}


@router.post("/api/proactive/accept")
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
    from app.services.agent.proactive import get_phase7
    sug = get_phase7().accept(sid)
    if not sug:
        raise HTTPException(status_code=404, detail="Unknown or already-resolved suggestion")
    return {"ok": True, "suggestion": sug}


@router.post("/api/proactive/dismiss")
async def api_proactive_dismiss(request: Request):
    """Dismiss a suggestion without running it."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    sid = str(body.get("id") or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="Missing suggestion id")
    from app.services.agent.proactive import get_phase7
    ok = get_phase7().dismiss(sid)
    if ok and bool(body.get("never")):
        action = str(body.get("action") or "").strip()
        if action:
            get_phase7().set_consent(action, "deny")
    return {"ok": bool(ok)}


@router.post("/api/proactive/consent")
async def api_proactive_consent(request: Request):
    """Set consent mode (ask/allow/deny) for a proactive action."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    action = str(body.get("action") or "").strip()
    mode = str(body.get("mode") or "").strip()
    from app.services.agent.proactive import get_phase7
    ok = get_phase7().set_consent(action, mode)
    if not ok:
        raise HTTPException(status_code=400, detail="Invalid action or mode")
    return {"ok": True, "action": action.lower(), "mode": mode}
