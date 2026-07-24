"""Request timing middleware and CORS configuration."""

import time
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger("J.A.R.V.I.S")

# Endpoints the UI polls on a timer (the Control Center hits these every ~2s).
# Logging each hit floods the console with useless noise, so skip them. The
# slow ones still log: see the SLOW_REQUEST_SECONDS guard below.
QUIET_REQUEST_PATHS = {"/api/watcher/state", "/api/dashboard/state", "/api/activity/recent",
                       "/api/activity/frontend-ack"}

# Even a quiet endpoint gets logged if it is unusually slow (helps catch real
# problems without the per-2s spam).
SLOW_REQUEST_SECONDS = 1.0


class TimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        t0 = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - t0
        path = request.url.path
        # The browser must not reuse an old activity-panel script after an
        # upgrade. Stale frontend assets made valid backend events invisible.
        if path.startswith("/jarvis/") or path.startswith("/app/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        noisy = path in QUIET_REQUEST_PATHS
        if not noisy:
            logger.info("[REQUEST] %s %s -> %s (%.3fs)", request.method, path, response.status_code, elapsed)
        elif response.status_code >= 400 or elapsed >= SLOW_REQUEST_SECONDS:
            # Polling endpoint, but something is wrong (error or slow) -- worth a line.
            logger.warning("[REQUEST] %s %s -> %s (%.3fs)", request.method, path, response.status_code, elapsed)
        return response
