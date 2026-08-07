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
        # Frontend cache policy.  [M14 P12.4]
        #
        # This used to be a blanket no-store on everything under /jarvis and
        # /app, for a real reason: a browser holding an old activity-panel
        # script made valid backend events invisible, and the bug looked like a
        # backend bug. The cost was that every reload re-downloaded every
        # stylesheet and every module, forever.
        #
        # Now that every asset carries ONE ?v= string (see the comment in
        # web/index.html), the two cases can be told apart:
        #
        #   with ?v=   the URL changes whenever the content does, so it can be
        #              cached hard. immutable additionally stops the browser
        #              revalidating on a manual refresh.
        #   without ?v= mostly HTML, which is where the version strings LIVE.
        #              It must revalidate or a stale document would keep asking
        #              for stale assets and the version string would never
        #              arrive. no-cache means "ask first", not "do not store",
        #              so a 304 is still cheap.
        #
        # HTML is never immutable even if someone appends ?v= to a page URL --
        # caching a document that carries the cache-busting strings for a year
        # is how you ship an app nobody can update.
        if path.startswith("/jarvis/") or path.startswith("/app/") or path.startswith("/static/"):
            is_html = path.endswith(("/", ".html")) or "." not in path.rsplit("/", 1)[-1]
            # Only a successful response may be cached for a year. An immutable
            # 404 or 500 is unfixable: the browser stops asking, so a transient
            # error survives every reload short of Ctrl+Shift+R.
            cacheable = 200 <= response.status_code < 300
            if request.query_params.get("v") and not is_html and cacheable:
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
                # Starlette's MutableHeaders has __delitem__ but NOT pop(). Calling
                # pop() here raised AttributeError on every ?v= asset, so every
                # stylesheet and the JS entry point 500'd and the page rendered
                # with no CSS and no JS at all.
                if "pragma" in response.headers:
                    del response.headers["Pragma"]
            else:
                response.headers["Cache-Control"] = "no-cache, must-revalidate"
                response.headers["Pragma"] = "no-cache"
        noisy = path in QUIET_REQUEST_PATHS
        if not noisy:
            logger.info("[REQUEST] %s %s -> %s (%.3fs)", request.method, path, response.status_code, elapsed)
        elif response.status_code >= 400 or elapsed >= SLOW_REQUEST_SECONDS:
            # Polling endpoint, but something is wrong (error or slow) -- worth a line.
            logger.warning("[REQUEST] %s %s -> %s (%.3fs)", request.method, path, response.status_code, elapsed)
        return response
