"""J.A.R.V.I.S — Just A Rather Very Intelligent System.

Slim application entry-point. All heavy logic lives in:
  - app/core/   — shared state, middleware, streaming helpers, startup lifecycle
  - app/api/    — route modules (system, chat, tts, dashboard, proactive, usermodel, testing)
  - app/services/ — business logic (agent, google, watcher, etc.)
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import logging

from app.core.startup import lifespan
from app.core.middleware import TimingMiddleware

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger("J.A.R.V.I.S")

# Mirror the console log to data/debug_logs/server.log. Console output is lost
# the moment the terminal scrolls, and it carries things the per-session debug
# log does not: third-party tracebacks, COM warnings, uvicorn errors.
try:
    from logging.handlers import RotatingFileHandler
    from pathlib import Path as _Path

    _log_dir = _Path(__file__).resolve().parent.parent / "data" / "debug_logs"
    _log_dir.mkdir(parents=True, exist_ok=True)
    _server_log = RotatingFileHandler(
        _log_dir / "server.log", maxBytes=8 * 1024 * 1024, backupCount=2,
        encoding="utf-8",
    )
    _server_log.setLevel(logging.INFO)
    _server_log.setFormatter(logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)-20s | %(threadName)-18s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    ))
    logging.getLogger().addHandler(_server_log)
except Exception as _fh_err:  # noqa: BLE001 - never block startup on logging
    logger.warning("[STARTUP] server.log file handler not installed: %s", _fh_err)

# Silence the harmless comtypes COM-release access violation that pycaw/pywinauto
# objects raise from __del__ during GC on a non-owning thread. Must run before
# any COM object is created (i.e. before the watcher / audio / UIA start).
try:
    from app._com_guard import install_com_release_guard

    install_com_release_guard()
except Exception as _cg_err:  # noqa: BLE001 - never block startup on this
    logger.debug("[STARTUP] COM-release guard not installed: %s", _cg_err)

# Quiet noisy third-party loggers so the J.A.R.V.I.S logs stay clean and readable.
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

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
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

app.add_middleware(TimingMiddleware)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
from app.api.system import router as system_router      # noqa: E402
from app.api.chat import router as chat_router          # noqa: E402
from app.api.tts import router as tts_router            # noqa: E402
from app.api.dashboard import router as dashboard_router  # noqa: E402
from app.api.proactive import router as proactive_router  # noqa: E402
from app.api.usermodel import router as usermodel_router  # noqa: E402
from app.api.testing import router as testing_router    # noqa: E402
from app.api.reminders import router as reminders_router  # noqa: E402
from app.api.notes import router as notes_router          # noqa: E402

app.include_router(system_router)
app.include_router(chat_router)
app.include_router(tts_router)
app.include_router(dashboard_router)
app.include_router(proactive_router)
app.include_router(usermodel_router)
app.include_router(testing_router)
app.include_router(reminders_router)
app.include_router(notes_router)

# ---------------------------------------------------------------------------
# Static files & root redirect
# ---------------------------------------------------------------------------
_web_dir = Path(__file__).resolve().parent.parent / "web"

# NOTE: the /jarvis/c/<session_id> deep-link route lives in app/api/dashboard.py
# and is registered above with the other routers -- i.e. BEFORE these mounts, so
# it wins the match instead of 404ing on StaticFiles.

if _web_dir.exists():
    # Canonical: serve the JARVIS app UI directly at /jarvis (no redirect to /app).
    app.mount("/jarvis", StaticFiles(directory=str(_web_dir), html=True), name="frontend")
    # Keep /app working too: the frontend JS hardcodes a few /app/* asset paths
    # (e.g. /app/audio/*, /app/api-monitor.html); removing it would break them.
    app.mount("/app", StaticFiles(directory=str(_web_dir), html=True), name="frontend_app")


@app.get("/")
async def root_redirect():
    return RedirectResponse(url="/jarvis/", status_code=302)


# ---------------------------------------------------------------------------
# Dev runner
# ---------------------------------------------------------------------------
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
