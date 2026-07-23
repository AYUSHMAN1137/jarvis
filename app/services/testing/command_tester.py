"""Command Tester -- bulk, live, one-by-one command testing tool (backend core).

WHAT IT DOES
------------
Runs a list of natural-language commands ONE-BY-ONE through the EXACT same
pipeline the `/chat/jarvis/stream` endpoint uses
(`chat_service.process_jarvis_message_stream`). So every command runs for real
-- real tools, real watcher, real planner -- exactly like a user typed it.

For each command it produces an HONEST verdict using a verification ladder that
NEVER trusts the assistant's narration ("yes, I opened YouTube"):

  T1  watcher/vision   -> the Phase 4 Checker's `verified` bus event
                          (deterministic ground truth: window/setting/vision).
  T2  tool-result      -> a real tool ran and returned a non-ERROR observation.
  T3  llm-judge (soft) -> ONLY when nothing observable ran (pure chat/Q&A).
                          Evidence = tool outputs + reply text. Marked SOFT.
  --  UNVERIFIED       -> honest "could not verify". Never a fake PASS.

Risky/dangerous commands are SKIPPED: we never auto-confirm, so the agent's
confirm-gate naturally stops them and we record SKIPPED (risky) + a note.

LOGS
----
While a session runs we attach a logging handler to the ROOT logger using the
exact same format as the console, so the captured log is terminal-identical.
Each session's log is downloadable as its own file (logs.txt, logs1.txt, ...).

Everything here is fail-soft and isolated from the core agent/planner/executor.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger("J.A.R.V.I.S")

# Console format -- MUST match logging.basicConfig in app/main.py so captured
# logs are byte-for-byte what the terminal shows.
_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

# How long to wait (per command) for the async Checker `verified` event to land
# after the command's stream finishes. Verification runs on a background thread,
# so the verdict can arrive a beat after the reply.
_VERIFY_SETTLE_MAX = 6.0      # hard cap
_VERIFY_SETTLE_QUIET = 0.5    # stop early after this much silence (once we have >=1)
_VERIFY_POLL = 0.1

_MAX_SESSIONS_KEPT = 25       # cap in-memory session logs
_MAX_COMMANDS = 200           # safety cap per run

# Section headers accidentally pasted into a command list look like
# "Files & folders — 23-34" (a label + an item-number range), not an actual
# instruction. Skip those so they aren't run as bogus commands (seen in logs).
_HEADER_RANGE_RE = re.compile(r"[\u2014\u2013-]\s*\d+\s*[\u2014\u2013-]\s*\d+\s*$")


def _looks_like_header(line: str) -> bool:
    s = (line or "").strip()
    if not s:
        return False
    # "Label — 23-34" style section header (short label ending in a range).
    return bool(_HEADER_RANGE_RE.search(s)) and len(s.split()) <= 8

# Tools whose successful return does NOT prove the real-world effect happened.
# They hand a frontend action to the browser (open/search/play) or just open a
# Settings page; the server cannot observe whether the video actually played,
# the results loaded, or a setting changed. We refuse a confident T2 PASS for
# these -- with no watcher/vision verdict they become an honest UNVERIFIED
# instead of a false PASS (e.g. "video play nahi hua" but shown PASS).
_WEAK_TOOLS = {
    "open_website", "play_on_youtube", "search_google", "search_youtube",
    "open_settings_page",
}

# A do_multistep preview that signals the plan did NOT fully succeed / paused.
_MULTISTEP_FAIL_HINTS = ("stopped early", "not everything worked")
_MULTISTEP_RISKY_HINTS = ("paused before a risky", "needs your confirmation",
                          "plan paused for confirmation")

# All-providers connectivity errors -> environment problem (UNVERIFIED), never a
# command FAIL (e.g. Wi-Fi got turned off mid-run and the LLM lost the network).
_NETWORK_FAIL_HINTS = (
    "connection error", "api key(s) failed", "failed to connect",
    "max retries exceeded", "getaddrinfo failed",
    "temporary failure in name resolution", "network is unreachable",
)

# Log records dropped from the captured session log -- HTTP/access noise that
# buries the real agent reasoning. Keeps the downloadable log relevant.
_LOG_DROP_LOGGERS = ("uvicorn.access",)
_LOG_DROP_SUBSTRINGS = (
    "/api/key-monitor", "/favicon.ico", "GET /static", "GET /assets",
    "/api/test-session/",
)


def _clean(s: Any, limit: int = 200) -> str:
    return " ".join(str(s if s is not None else "").split())[:limit]


class _SessionLogHandler(logging.Handler):
    """Captures formatted log records into an in-memory buffer (thread-safe)."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT))
        self._lines: List[str] = []
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        try:
            # Drop HTTP-access / polling noise so the captured log stays focused
            # on the real agent reasoning (well-organized + relevant).
            if record.name in _LOG_DROP_LOGGERS:
                return
            raw_msg = record.getMessage()
            if any(s in raw_msg for s in _LOG_DROP_SUBSTRINGS):
                return
            line = self.format(record)
            with self._lock:
                self._lines.append(line)
        except Exception:  # noqa: BLE001 - logging must never raise
            pass

    def text(self) -> str:
        with self._lock:
            if not self._lines:
                return ""
            return "\n".join(self._lines) + "\n"

    def line_count(self) -> int:
        with self._lock:
            return len(self._lines)


class CommandTester:
    """Singleton orchestrator for bulk live command testing."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # id -> session dict (OrderedDict so we can evict oldest)
        self._sessions: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._seq = 0                       # global session counter (for file label)
        self._subscribed = False
        # The currently-running command's event collector (or None when idle).
        # Commands run strictly one-by-one, so a single slot is safe + simple.
        self._active_events: Optional[List[Dict[str, Any]]] = None
        self._active_at = 0.0

    # -- bus wiring (collect REAL verification signals) ------------------ #
    def _ensure_subscribed(self) -> None:
        if self._subscribed:
            return
        try:
            from app.services.agent.phase4.event_bus import get_event_bus
            bus = get_event_bus()
            bus.subscribe("verified", lambda p: self._collect("verified", p))
            bus.subscribe("action.done", lambda p: self._collect("action.done", p))
            self._subscribed = True
            logger.info("[TESTER] subscribed to verification bus (verified, action.done).")
        except Exception as e:  # noqa: BLE001 - bus optional; T2 still works
            logger.debug("[TESTER] bus subscribe failed (continuing): %s", _clean(e))

    def _collect(self, kind: str, payload: dict) -> None:
        with self._lock:
            if self._active_events is not None:
                ev = {"kind": kind, "at": time.time()}
                try:
                    ev.update(dict(payload or {}))
                except Exception:  # noqa: BLE001
                    pass
                self._active_events.append(ev)
                self._active_at = time.time()

    # -- session bookkeeping -------------------------------------------- #
    def _new_session(self) -> Dict[str, Any]:
        with self._lock:
            n = self._seq
            self._seq += 1
            sid = "ts_%d_%d" % (int(time.time()), n)
            label = "logs.txt" if n == 0 else "logs%d.txt" % n
            sess = {
                "id": sid,
                "label": label,
                "created": time.time(),
                "handler": _SessionLogHandler(),
                "results": [],
                "summary": None,
                "done": False,
            }
            self._sessions[sid] = sess
            while len(self._sessions) > _MAX_SESSIONS_KEPT:
                self._sessions.popitem(last=False)
            return sess

    def get_session(self, sid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._sessions.get(sid)

    def get_log_text(self, sid: str) -> Optional[str]:
        sess = self.get_session(sid)
        if not sess:
            return None
        try:
            return sess["handler"].text()
        except Exception:  # noqa: BLE001
            return ""

    def get_log_label(self, sid: str) -> str:
        sess = self.get_session(sid)
        return sess["label"] if sess else "logs.txt"

    # -- SSE helper ----------------------------------------------------- #
    @staticmethod
    def _sse(obj: dict) -> str:
        return "data: %s\n\n" % json.dumps(obj, ensure_ascii=False)

    # -- main entrypoint (sync generator -> StreamingResponse) ---------- #
    def run_stream(
        self,
        chat_service: Any,
        commands: List[str],
        *,
        on_risky: str = "skip",      # "skip" (locked default) -- never auto-confirm
        on_fail: str = "continue",   # "continue" (locked default) | "stop"
        judge: bool = True,          # enable the T3 soft LLM-judge fallback
    ) -> Iterator[str]:
        """Yield SSE strings: one `session` event, then per-command
        `command_start` / `command_result`, then a final `summary`."""
        cmds = [c.strip() for c in (commands or [])
                if c and c.strip() and not _looks_like_header(c.strip())][:_MAX_COMMANDS]
        sess = self._new_session()
        handler = sess["handler"]
        root = logging.getLogger()
        self._ensure_subscribed()
        # Capture must work even if the ambient log level is raised. In prod the
        # console is already INFO (main.py basicConfig), so this is a no-op there;
        # it only guarantees the captured log is terminal-identical regardless.
        _prev_root_level = root.level
        if root.getEffectiveLevel() > logging.INFO:
            root.setLevel(logging.INFO)
        root.addHandler(handler)
        try:
            yield self._sse({
                "event": "session", "session_id": sess["id"],
                "log_label": sess["label"], "total": len(cmds),
            })
            logger.info("[TESTER] ===== Test session %s START -- %d command(s) =====",
                        sess["id"], len(cmds))

            if not cmds:
                summary = self._summary([], stopped=False)
                summary["phases"] = self._phase_rollup([])
                sess["summary"] = summary
                sess["done"] = True
                yield self._sse({"event": "summary", "session_id": sess["id"],
                                 "log_label": sess["label"], **summary})
                return

            chat_session_id = self._make_chat_session(chat_service)
            results: List[Dict[str, Any]] = []
            stopped = False

            for i, cmd in enumerate(cmds, start=1):
                yield self._sse({"event": "command_start", "index": i,
                                 "total": len(cmds), "command": cmd})
                res = self._run_one(chat_service, chat_session_id, i, cmd,
                                    on_risky=on_risky, judge=judge)
                results.append(res)
                sess["results"] = list(results)
                yield self._sse({"event": "command_result", **res})

                if res["status"] == "FAIL" and on_fail == "stop":
                    stopped = True
                    logger.info("[TESTER] on_fail=stop -- halting after command %d.", i)
                    # Record the remaining commands as not-run (honest).
                    for j, rest in enumerate(cmds[i:], start=i + 1):
                        skipped = {
                            "index": j, "command": rest, "status": "SKIPPED",
                            "tier": "-", "source": "not-run",
                            "reason": "skipped -- a previous command failed (stop mode)",
                            "soft": False, "tools": [], "reply": "",
                        }
                        results.append(skipped)
                        yield self._sse({"event": "command_result", **skipped})
                    break

            summary = self._summary(results, stopped=stopped)
            summary["phases"] = self._phase_rollup(results)
            sess["results"] = list(results)
            sess["summary"] = summary
            sess["done"] = True
            ph = summary["phases"]
            logger.info("[TESTER] phases: cache_hits=%s habits=%s(trusted %s) pending=%s",
                        ph.get("cache_hits"), ph.get("habits"),
                        ph.get("trusted_habits"), ph.get("pending_suggestions"))
            logger.info("[TESTER] ===== Session %s DONE -- %s =====",
                        sess["id"], summary["line"])
            yield self._sse({"event": "summary", "session_id": sess["id"],
                             "log_label": sess["label"], **summary})
        except Exception as e:  # noqa: BLE001 - never let the stream crash hard
            logger.warning("[TESTER] run_stream error: %s", _clean(e))
            yield self._sse({"event": "error", "message": _clean(e)})
        finally:
            try:
                root.removeHandler(handler)
                root.setLevel(_prev_root_level)
            except Exception:  # noqa: BLE001
                pass
            with self._lock:
                self._active_events = None

    # -- per-command execution ------------------------------------------ #
    def _make_chat_session(self, chat_service: Any) -> str:
        try:
            return chat_service.get_or_create_session(None)
        except Exception:  # noqa: BLE001
            return "tester-%d" % int(time.time())

    def _run_one(self, chat_service: Any, chat_session_id: str, index: int,
                 cmd: str, *, on_risky: str, judge: bool) -> Dict[str, Any]:
        logger.info("[TESTER] ---- [%d] RUN: %s ----", index, _clean(cmd, 160))

        # Open a fresh collector for this command's verification events.
        with self._lock:
            self._active_events = []
            self._active_at = time.time()

        tools: List[Dict[str, Any]] = []   # [{tool, ok, preview}]
        awaiting_risky: Optional[str] = None
        reply_parts: List[str] = []
        crashed: Optional[str] = None
        cached: bool = False

        try:
            for chunk in chat_service.process_jarvis_message_stream(chat_session_id, cmd):
                if isinstance(chunk, dict):
                    act = chunk.get("_activity")
                    if isinstance(act, dict):
                        et = act.get("event")
                        if et == "tool_result":
                            tools.append({
                                "tool": act.get("tool"),
                                "ok": bool(act.get("ok")),
                                "preview": _clean(act.get("preview"), 160),
                            })
                        elif et == "awaiting_confirmation":
                            awaiting_risky = act.get("tool") or "sensitive action"
                        elif et == "cache_hit":
                            cached = True
                    # ignore _actions / _search_results / _background_tasks
                    continue
                if isinstance(chunk, str):
                    reply_parts.append(chunk)
        except Exception as e:  # noqa: BLE001
            crashed = _clean(e, 200)
            logger.warning("[TESTER] [%d] command crashed: %s", index, crashed)

        # Never let a pending confirmation leak into the NEXT command.
        try:
            pend = getattr(chat_service, "_pending_confirmations", None)
            if isinstance(pend, dict):
                pend.pop(chat_session_id, None)
        except Exception:  # noqa: BLE001
            pass

        # Wait briefly for the async Checker verdict to land.
        self._settle_verification(expect_tools=len(tools))
        with self._lock:
            events = list(self._active_events or [])
            self._active_events = None
        reply = "".join(reply_parts).strip()

        verdict = self._synthesize(
            cmd=cmd, crashed=crashed, awaiting_risky=awaiting_risky,
            on_risky=on_risky, tools=tools, events=events, reply=reply, judge=judge,
        )
        result = {
            "index": index, "command": cmd,
            "tools": [t.get("tool") for t in tools],
            "reply": _clean(reply, 400),
            "cached": cached,
            **verdict,
        }
        # Structured, relevant per-command block (keeps the downloadable log
        # easy to scan: what ran, what we checked, and the honest verdict).
        tool_summary = ", ".join(
            "%s%s" % (t.get("tool"), "" if t.get("ok") else "!ERR") for t in tools
        ) or "(none)"
        logger.info("[TESTER] [%d] tools: %s", index, _clean(tool_summary, 200))
        check_summary = "; ".join(
            "%s=%s(%s)" % (e.get("tool") or "?", e.get("verdict") or "?",
                           _clean(e.get("reason") or e.get("evidence"), 48))
            for e in events if e.get("kind") == "verified"
        )
        if check_summary:
            logger.info("[TESTER] [%d] checks: %s", index, _clean(check_summary, 240))
        logger.info("[TESTER] [%d] VERDICT: %s (%s, via %s) -- %s",
                    index, result["status"], result["tier"], result["source"],
                    result["reason"])
        return result

    def _settle_verification(self, expect_tools: int) -> None:
        """Poll until verified events stop arriving (or hard cap)."""
        if expect_tools <= 0:
            # No tools ran -> no watcher verdict expected; tiny grace only.
            time.sleep(0.05)
            return
        start = time.time()
        while True:
            now = time.time()
            if now - start >= _VERIFY_SETTLE_MAX:
                return
            with self._lock:
                evs = self._active_events or []
                verified = [e for e in evs if e.get("kind") == "verified"]
                last_at = self._active_at
            # Enough verdicts collected -> done.
            if len(verified) >= expect_tools:
                return
            # Quiet period after at least one verdict -> done.
            if verified and (now - last_at) >= _VERIFY_SETTLE_QUIET:
                return
            time.sleep(_VERIFY_POLL)

    # -- verdict ladder (the heart of honest verification) -------------- #
    def _synthesize(self, *, cmd: str, crashed: Optional[str],
                    awaiting_risky: Optional[str], on_risky: str,
                    tools: List[Dict[str, Any]], events: List[Dict[str, Any]],
                    reply: str, judge: bool) -> Dict[str, Any]:
        # 0) Hard crash in the pipeline.
        if crashed:
            if self._is_network_failure(crashed):
                return {"status": "UNVERIFIED", "tier": "-", "source": "environment",
                        "reason": "network/provider error, not a command fault: %s"
                                  % _clean(crashed, 120), "soft": False}
            return {"status": "FAIL", "tier": "-", "source": "crash",
                    "reason": "pipeline error: %s" % crashed, "soft": False}

        # 1) Risky / dangerous -> skipped (we never auto-confirm).
        if awaiting_risky:
            return {"status": "SKIPPED", "tier": "-", "source": "risky",
                    "reason": "sensitive action (%s) -- skipped, needs confirmation"
                              % awaiting_risky, "soft": False}

        # Correlate verification events to THIS command's tools. A late verdict
        # from a previous command (different tool) can land while this command's
        # collector is open -- without this filter it bleeds in and mislabels
        # the next command (e.g. cmd3's settings FAIL attaching to cmd4).
        verified_all = [e for e in events if e.get("kind") == "verified"]
        tool_names = {str(tl.get("tool")) for tl in tools if tl.get("tool")}
        if tool_names:
            verified = [e for e in verified_all
                        if not e.get("tool") or str(e.get("tool")) in tool_names]
        else:
            verified = verified_all
        fails = [e for e in verified if str(e.get("verdict")).upper() == "FAIL"]
        passes = [e for e in verified if str(e.get("verdict")).upper() == "PASS"]

        # 2) T1 -- deterministic watcher/vision verdict (ground truth).
        if fails:
            e = fails[0]
            return {"status": "FAIL", "tier": "T1",
                    "source": e.get("source") or "watcher",
                    "reason": _clean(e.get("reason") or e.get("evidence")
                                     or "verification failed"), "soft": False}
        if passes:
            e = passes[0]
            return {"status": "PASS", "tier": "T1",
                    "source": e.get("source") or "watcher",
                    "reason": _clean(e.get("reason") or e.get("evidence")
                                     or "verified by watcher/vision"), "soft": False}

        # 3) T2 -- a real tool ran and returned a non-ERROR observation.
        if tools:
            errored = [t for t in tools if not t.get("ok")]
            if errored:
                t = errored[0]
                if self._is_network_failure(t.get("preview")):
                    return {"status": "UNVERIFIED", "tier": "T2", "source": "environment",
                            "reason": "network/provider error, not a command fault: %s"
                                      % _clean(t.get("preview"), 120), "soft": False}
                return {"status": "FAIL", "tier": "T2", "source": "tool-result",
                        "reason": "tool '%s' returned an error: %s"
                                  % (t.get("tool"), _clean(t.get("preview"), 120)),
                        "soft": False}
            # do_multistep reports its OWN honest outcome -- a plan that stopped
            # early must not become a PASS just because the tool returned.
            for t in tools:
                if str(t.get("tool")) == "do_multistep":
                    pv = str(t.get("preview") or "").lower()
                    if any(h in pv for h in _MULTISTEP_FAIL_HINTS):
                        return {"status": "FAIL", "tier": "T2", "source": "tool-result",
                                "reason": "multi-step plan stopped early: %s"
                                          % _clean(t.get("preview"), 120), "soft": False}
                    if any(h in pv for h in _MULTISTEP_RISKY_HINTS):
                        return {"status": "SKIPPED", "tier": "T2", "source": "tool-result",
                                "reason": "multi-step plan paused for confirmation",
                                "soft": False}
            # Strong tools (real OS effects) that returned ok are a fair PASS.
            # Weak/frontend tools only *requested* a browser action or opened a
            # Settings page -- the server can't see if it truly happened, so we
            # stay honest (UNVERIFIED) instead of faking a PASS.
            strong = [t for t in tools if str(t.get("tool")) not in _WEAK_TOOLS]
            if strong:
                names = ", ".join([str(t.get("tool")) for t in strong][:4])
                return {"status": "PASS", "tier": "T2", "source": "tool-result",
                        "reason": "tool(s) ran ok: %s" % names, "soft": False}
            weak_names = ", ".join([str(t.get("tool")) for t in tools][:4])
            return {"status": "UNVERIFIED", "tier": "T2", "source": "frontend",
                    "reason": "requested frontend action (%s) -- can't confirm from the "
                              "server that it happened on screen" % weak_names,
                    "soft": False}

        # 4) T3 -- nothing observable ran (pure chat/Q&A). Soft LLM-judge.
        if judge:
            jv = self._llm_judge(cmd, reply)
            if jv is not None:
                status, reason = jv
                if status in ("PASS", "FAIL"):
                    return {"status": status, "tier": "T3", "source": "llm-judge",
                            "reason": reason, "soft": True}
            # judge unclear / unavailable -> stay honest.
        return {"status": "UNVERIFIED", "tier": "-", "source": "none",
                "reason": "no observable signal to verify (no tool ran / no watcher state)",
                "soft": False}

    @staticmethod
    def _is_network_failure(text: Any) -> bool:
        """True if text looks like an all-providers network/connectivity error.

        Used so a command that 'failed' only because Wi-Fi was turned off
        mid-run (or the network blipped) is reported as an honest environment
        UNVERIFIED, never as a command FAIL.
        """
        low = str(text or "").lower()
        return any(h in low for h in _NETWORK_FAIL_HINTS)

    # -- T3 soft LLM-judge (mirrors planner's fail-soft raw-client use) -- #
    def _llm_judge(self, cmd: str, reply: str):
        """Best-effort judge. Evidence = the command + the assistant reply.

        Returns ("PASS"|"FAIL"|"UNCLEAR", reason) or None if unavailable.
        Fail-soft: any error (no keys, network off, rate-limit) returns None.
        """
        try:
            import config as _cfg
            from app.services import llm_providers as P
        except Exception:  # noqa: BLE001
            return None
        model = getattr(_cfg, "GEMINI_MODEL", None) or "gemini-2.0-flash"
        system = (
            "You are a STRICT QA judge for a voice assistant. Given a user COMMAND "
            "and the assistant's REPLY, decide if the command was actually handled. "
            "You have NO other evidence. CRITICAL: the reply is the assistant's own "
            "CLAIM, not proof -- a confident 'Done!' is NOT evidence the action "
            "happened. Only PASS when the command is a pure question / chit-chat that "
            "the reply genuinely answers with real content. For any command implying "
            "a real-world action (open / play / search / change a setting), the reply "
            "alone canNOT prove success -> verdict UNCLEAR. If the assistant ASKED "
            "the user to confirm a risky/destructive action before doing it (a "
            "safety pause), that is NOT a failure -> verdict UNCLEAR. Be skeptical of empty, "
            "generic, or hand-wavy replies. Respond with STRICT JSON: "
            '{"verdict":"PASS|FAIL|UNCLEAR","reason":"<=12 words"}.'
        )
        user = "COMMAND: %s\nREPLY: %s" % (_clean(cmd, 300), _clean(reply, 800))
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": user}]
        try:
            keys = P.ordered_keys()
        except Exception:  # noqa: BLE001
            keys = [0]
        for ki in (keys or [0])[:2]:
            try:
                client = P.make_raw_client(
                    ki, timeout=getattr(_cfg, "AGENT_REQUEST_TIMEOUT", 18))
                resp = client.chat.completions.create(
                    model=model, messages=messages, temperature=0.0)
                raw = (resp.choices[0].message.content or "").strip()
                return self._parse_judge(raw)
            except Exception as e:  # noqa: BLE001
                if P.is_rate_limit_error(e):
                    try:
                        P.trip(ki)
                    except Exception:  # noqa: BLE001
                        pass
                    continue
                logger.debug("[TESTER] llm-judge key %s failed: %s", ki, _clean(e))
                continue
        return None

    @staticmethod
    def _parse_judge(raw: str):
        if not raw:
            return None
        text = raw.strip()
        # strip markdown code fences if present
        if text.startswith("```"):
            text = text.strip("`")
            nl = text.find("\n")
            if nl != -1:
                text = text[nl + 1:]
        verdict = None
        reason = ""
        try:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                obj = json.loads(text[start:end + 1])
                verdict = str(obj.get("verdict", "")).upper().strip()
                reason = _clean(obj.get("reason", ""), 120)
        except Exception:  # noqa: BLE001
            verdict = None
        if verdict not in ("PASS", "FAIL", "UNCLEAR"):
            up = text.upper()
            if "PASS" in up:
                verdict = "PASS"
            elif "FAIL" in up:
                verdict = "FAIL"
            else:
                verdict = "UNCLEAR"
        if not reason:
            reason = "judged from reply (soft)"
        return (verdict, reason)

    # -- phase rollup (Phase 6/7/8 visibility in the session summary) --- #
    def _phase_rollup(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Best-effort Phase 6/7/8 stats for the session summary. Fail-soft:
        any missing phase simply contributes zeros."""
        roll: Dict[str, Any] = {
            "cache_hits": sum(1 for r in results if r.get("cached")),
            "habits": 0, "trusted_habits": 0, "facts": 0,
            "pending_suggestions": 0, "suggested": 0,
        }
        # Phase 8 -- user model: fold in this session's observations, then read.
        try:
            from app.services.agent.phase8 import get_phase8
            p8 = get_phase8()
            try:
                p8.aggregate_from_provider()
            except Exception:  # noqa: BLE001
                pass
            st8 = p8.stats() or {}
            roll["habits"] = int(st8.get("habits", 0) or 0)
            roll["trusted_habits"] = int(st8.get("trusted_habits", 0) or 0)
            roll["facts"] = int(st8.get("facts", 0) or 0)
        except Exception:  # noqa: BLE001
            pass
        # Phase 7 -- proactive engine.
        try:
            from app.services.agent.phase7 import get_phase7
            p7 = get_phase7()
            try:
                roll["pending_suggestions"] = len(p7.get_pending(50) or [])
            except Exception:  # noqa: BLE001
                pass
            st7 = p7.stats() or {}
            roll["suggested"] = int(st7.get("suggested", 0) or 0)
        except Exception:  # noqa: BLE001
            pass
        return roll

    # -- summary -------------------------------------------------------- #
    @staticmethod
    def _summary(results: List[Dict[str, Any]], *, stopped: bool) -> Dict[str, Any]:
        total = len(results)
        passed = sum(1 for r in results if r["status"] == "PASS")
        failed = sum(1 for r in results if r["status"] == "FAIL")
        skipped = sum(1 for r in results if r["status"] == "SKIPPED")
        unverified = sum(1 for r in results if r["status"] == "UNVERIFIED")
        soft = sum(1 for r in results if r.get("soft"))
        parts = ["%d/%d PASS" % (passed, total)]
        if failed:
            parts.append("%d FAIL" % failed)
        if skipped:
            parts.append("%d SKIP" % skipped)
        if unverified:
            parts.append("%d UNVERIFIED" % unverified)
        line = " \u00b7 ".join(parts)
        failed_cmds = [r["command"] for r in results if r["status"] == "FAIL"]
        return {
            "total": total, "passed": passed, "failed": failed,
            "skipped": skipped, "unverified": unverified, "soft": soft,
            "stopped": stopped, "line": line, "failed_commands": failed_cmds,
        }


# --------------------------------------------------------------------------- #
# singleton
# --------------------------------------------------------------------------- #
_tester: Optional[CommandTester] = None
_tester_lock = threading.Lock()


def get_command_tester() -> CommandTester:
    global _tester
    if _tester is None:
        with _tester_lock:
            if _tester is None:
                _tester = CommandTester()
    return _tester
