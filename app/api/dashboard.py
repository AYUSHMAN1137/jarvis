"""Dashboard and watcher routes: /dashboard, /watcher, /api/dashboard/state, /api/watcher/state."""

import time
import logging
from collections import deque
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from starlette.requests import Request

import app.core.state as state
from app.services.api_key_monitor import get_api_key_monitor

logger = logging.getLogger("J.A.R.V.I.S")

router = APIRouter()
_frontend_acks = deque(maxlen=100)

_WEB_DIR = Path(__file__).resolve().parent.parent.parent / "web"


@router.get("/jarvis/c/{session_id}")
@router.get("/app/c/{session_id}")
async def conversation_deep_link(session_id: str):
    """Serve the app shell for a client-side conversation URL.

    /jarvis/c/<session_id> is a frontend route, so a hard refresh or a pasted
    link would otherwise 404 on the StaticFiles mount. Registered with the
    routers (before the mounts in main.py) so it wins the match. index.html
    carries a <base href> so its relative assets still resolve one level deeper.

    session_id is never touched here -- it is not used to build a path, and the
    frontend falls back to a fresh chat when the conversation does not exist.
    """
    index = _WEB_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="UI not available")
    return FileResponse(str(index), media_type="text/html")


@router.post("/api/activity/frontend-ack")
async def frontend_action_ack(request: Request):
    """Record that the browser attempted a server-dispatched frontend action.

    This is transport evidence only; it does not claim an external page loaded.
    Section 7: frontend action acknowledgement path.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    dispatch_id = str(body.get("dispatch_id") or "")[:64]
    action_id = str(body.get("action_id") or "")[:64]
    if not dispatch_id or not action_id:
        raise HTTPException(status_code=400, detail="Missing dispatch correlation")
    row = {
        "time": time.time(), "dispatch_id": dispatch_id,
        "execution_id": str(body.get("execution_id") or "")[:64],
        "action_id": action_id, "attempted": bool(body.get("attempted")),
        "accepted": bool(body.get("accepted")),
        "error": str(body.get("error") or "")[:160],
    }
    _frontend_acks.append(row)
    # Wire to Phase 4 coordinator for verification correlation
    try:
        from app.services.agent.checker import get_phase4
        get_phase4().acknowledge_dispatch(
            dispatch_id, attempted=row["attempted"],
            accepted=row["accepted"], error=row["error"])
    except Exception:  # noqa: BLE001 - fail-soft
        pass
    logger.info("[FRONTEND-ACK] dispatch=%s action=%s attempted=%s accepted=%s%s",
                dispatch_id[:12], action_id[:12], row["attempted"], row["accepted"],
                (" error=" + row["error"]) if row["error"] else "")
    return {"ok": True}

_WATCHER_DASH_FILE = Path(__file__).resolve().parent.parent / "static" / "watcher_dashboard.html"
_CONTROL_DASH_FILE = Path(__file__).resolve().parent.parent / "static" / "dashboard.html"


@router.get("/api/watcher/state")
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


@router.get("/api/dashboard/state")
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
        from app.services.agent.checker import get_phase4
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
        from app.services.agent.cache import get_phase6
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
        from app.services.agent.proactive import get_phase7
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
        from app.services.agent.personalization import get_phase8
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
    sysinfo = {"agent_loop": state.agent_loop is not None, "vision": state.vision_service is not None}
    try:
        from app.services.memory_service import get_memory
        sysinfo["memory"] = get_memory() is not None
    except Exception:  # noqa: BLE001
        sysinfo["memory"] = False
    out["system"] = sysinfo
    return out


@router.get("/api/activity/recent")
async def api_activity_recent():
    """Small activity feed for the main JARVIS side panel.

    It exposes only final background verification and cache decisions. Normal
    request progress continues to arrive through the existing chat SSE stream.
    """
    out = {"timestamp": time.time(), "verification": [], "cache": []}
    try:
        from app.services.agent.checker import get_phase4
        out["verification"] = get_phase4().recent_activity(20)
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.services.agent.cache import get_phase6
        out["cache"] = get_phase6().recent_activity(20)
    except Exception:  # noqa: BLE001
        pass
    return out


@router.get("/dashboard")
async def control_center():
    """Unified Control Center: app + watcher + checker + learner + skills."""
    try:
        html = _CONTROL_DASH_FILE.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        html = f"<h1>Control Center unavailable</h1><p>{e}</p>"
    return HTMLResponse(html)


@router.get("/watcher")
async def watcher_dashboard():
    """Serve the focused watcher (Phase 1) live dashboard."""
    try:
        html = _WATCHER_DASH_FILE.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        html = f"<h1>Watcher dashboard unavailable</h1><p>{e}</p>"
    return HTMLResponse(html)
