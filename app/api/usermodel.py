"""User model routes: /api/usermodel/knowledge, /api/usermodel/forget."""

import logging

from fastapi import APIRouter, HTTPException
from starlette.requests import Request

logger = logging.getLogger("J.A.R.V.I.S")

router = APIRouter()


@router.get("/api/usermodel/knowledge")
async def api_usermodel_knowledge():
    """Everything JARVIS has learned about the user (facts/aliases/habits)."""
    from app.services.agent.personalization import get_phase8
    p8 = get_phase8()
    return {"knowledge": p8.knowledge_summary(), "stats": p8.stats()}


@router.post("/api/usermodel/forget")
async def api_usermodel_forget(request: Request):
    """Forget a fact, alias, habit, or everything (scope=all)."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    scope = str(body.get("scope") or "").strip().lower()
    from app.services.agent.personalization import get_phase8
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
