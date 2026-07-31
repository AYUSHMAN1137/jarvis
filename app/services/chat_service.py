import base64
import json
import logging
import time
from pathlib import Path
from typing import List, Optional, Dict, Iterator, Any, Union
import uuid
import threading

import os
import re
from datetime import datetime, timezone

from config import (
    CHATS_DATA_DIR,
    CAMERA_CAPTURES_DIR,
    MAX_CHAT_HISTORY_TURNS,
    GROQ_API_KEYS,
    HISTORY_PAGE_SIZE,
    HISTORY_MAX_PAGE_SIZE,
    HISTORY_TITLE_MAX_CHARS,
    HISTORY_PREVIEW_MAX_CHARS,
)
from app.models import ChatMessage
from app.services.groq_service import GroqService
from app.services.realtime_service import RealtimeGroqService
from app.services.vision_service import VisionService
from app.services.agent.agent_loop import AgentLoop
from app.services.resolver import (
    ACTING_KINDS, KIND_ACTION, KIND_KNOWLEDGE_QUESTION, KIND_MIXED, KIND_VISUAL,
    KIND_WEB_QUESTION,
)
from app.utils.key_rotation import get_next_key_pair
from app.services.debug_logger import dbg

logger = logging.getLogger("J.A.R.V.I.S")

CAMERA_BYPASS_TOKEN = "TTCAMTOKENTT"
SAVE_EVERY_N_CHUNKS = 5

# Resolver `kind` -> the legacy route word the frontend already understands.
# M13 changed the routing vocabulary; the SSE contract did not change with it,
# because web/script.js keys orb states, route colours and the search starter
# sound off `decision.query_type`.
_ROUTE_FOR_KIND = {
    KIND_ACTION: "task",
    KIND_MIXED: "mixed",
    KIND_VISUAL: "task",
    KIND_WEB_QUESTION: "realtime",
    KIND_KNOWLEDGE_QUESTION: "general",
}

# Persisted chat JSON schema. v1 == the original {session_id, messages} shape,
# which is still read without complaint; v2 adds title/timestamps/message_count.
CHAT_SCHEMA_VERSION = 2
DEFAULT_CHAT_TITLE = "New conversation"
_WHITESPACE_RE = re.compile(r"\s+")


def _now_iso() -> str:
    """Local-time ISO 8601 with offset, so the UI can group by day correctly."""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _mtime_iso(path: Path) -> str:
    try:
        ts = path.stat().st_mtime
    except OSError:
        return _now_iso()
    return datetime.fromtimestamp(ts, timezone.utc).astimezone().isoformat(timespec="seconds")


def _ctime_iso(path: Path) -> str:
    try:
        st = path.stat()
    except OSError:
        return _now_iso()
    # st_ctime is creation time on Windows; fall back to mtime elsewhere.
    ts = min(getattr(st, "st_ctime", st.st_mtime), st.st_mtime)
    return datetime.fromtimestamp(ts, timezone.utc).astimezone().isoformat(timespec="seconds")


def _clean_text(value: Any, limit: int) -> str:
    """Collapse whitespace and truncate at a word boundary when possible."""
    text = _WHITESPACE_RE.sub(" ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rstrip()
    space = cut.rfind(" ")
    if space >= limit // 2:
        cut = cut[:space].rstrip()
    return cut + "…"


def derive_title(messages: List[Any]) -> str:
    """Deterministic title from the first meaningful user message."""
    for msg in messages or []:
        role = getattr(msg, "role", None) or (msg.get("role") if isinstance(msg, dict) else None)
        if role != "user":
            continue
        content = getattr(msg, "content", None) or (msg.get("content") if isinstance(msg, dict) else None)
        text = _clean_text(content, HISTORY_TITLE_MAX_CHARS)
        if text and text != CAMERA_BYPASS_TOKEN:
            return text
    return DEFAULT_CHAT_TITLE

# Empty frontend-actions skeleton.
_EMPTY_ACTIONS = {
    "wopens": [], "plays": [], "images": [], "contents": [],
    "googlesearches": [], "youtubesearches": [], "cam": None, "panels": {},
}


def _save_camera_image(img_base64: str, session_id: str) -> Optional[Path]:
    if not img_base64 or not CAMERA_CAPTURES_DIR:
        return None
    raw = img_base64.split(",", 1)[-1] if "," in img_base64 else img_base64
    try:
        data = base64.b64decode(raw)
        if len(data) < 1000:
            logger.warning("[VISION] Captured image very small (%d bytes), may be invalid", len(data))
        ts = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        safe_id = (session_id or "").replace("/", "_")[:16] or "unknown"
        path = CAMERA_CAPTURES_DIR / f"cam_{safe_id}_{ts}.jpg"
        path.write_bytes(data)
        logger.info("[VISION] Saved camera capture: %s (%d bytes)", path.name, len(data))
        return path
    except Exception as e:
        logger.warning("[VISION] Failed to save camera image: %s", e)
        return None


class ChatService:
    def __init__(
        self,
        groq_service: GroqService,
        realtime_service: RealtimeGroqService = None,
        vision_service: VisionService = None,
        agent_loop: AgentLoop = None,
    ):
        self.groq_service = groq_service
        self.realtime_service = realtime_service
        self.vision_service = vision_service
        self.agent_loop = agent_loop
        self.sessions: Dict[str, List[ChatMessage]] = {}
        self._save_lock = threading.Lock()
        # session_id -> {"tool": str, "original_message": str} for dangerous-action confirms
        self._pending_confirmations: Dict[str, Dict[str, Any]] = {}
        # session_id -> {"goal": str, "verdict": str} -- the last real request, so
        # "nothing happened" can be re-run instead of answered with small talk.
        self._last_goals: Dict[str, Dict[str, Any]] = {}
        # session_id -> {"tool", "args", "verdict", "reason"} for the last action
        # actually executed. This is context the resolver reads: a complaint only
        # makes sense against what really happened, and its verdict.
        self._last_actions: Dict[str, Dict[str, Any]] = {}
        # session_id -> {"title", "created_at", "title_is_custom"}. Keeps a user
        # rename and the original created_at alive across per-turn resaves
        # without re-reading the file on every chunk flush.
        self._session_meta: Dict[str, Dict[str, Any]] = {}
        # Sessions that exist only to drive a stream and must never be written
        # to disk or surfaced as conversations (currently: the daily startup
        # brief, whose "user message" is an internal prompt, not something the
        # user typed).
        self._transient_sessions: set = set()

    # ===================== startup briefing helpers ===================== #
    def _get_startup_email_line(self) -> str:
        from app.services.agent.deps import deps
        gmail_service = deps.gmail_service
        if not gmail_service:
            return "Your email status is unavailable right now."
        try:
            unread_count = gmail_service.get_unread_count(allow_interactive=False)
            if unread_count == 0:
                return "Your inbox is clear, with no unread emails pending."
            if unread_count == 1:
                return "You currently have 1 unread email waiting."
            return f"You currently have {unread_count} unread emails waiting."
        except Exception as e:
            logger.info("[STARTUP-STREAM] Email status unavailable: %s", e)
            return "Your email status is unavailable right now."

    def _get_startup_calendar_line(self) -> str:
        from app.services.agent.deps import deps
        calendar_service = deps.calendar_service
        if not calendar_service:
            return "Your calendar status is unavailable right now."
        try:
            today_count = calendar_service.get_today_event_count(allow_interactive=False)
            if today_count == 0:
                return "Your calendar is clear for today."
            if today_count == 1:
                return "You have 1 event scheduled for today."
            return f"You have {today_count} events scheduled for today."
        except Exception as e:
            logger.info("[STARTUP-STREAM] Calendar status unavailable: %s", e)
            return "Your calendar status is unavailable right now."

    # ===================== session management ===================== #
    def _session_path(self, session_id: str) -> Path:
        """Filename for a session. The client never supplies a path -- only an
        already-validated session id, which is stripped of separators here."""
        safe_session_id = session_id.replace("-", "").replace(" ", "_")
        return CHATS_DATA_DIR / f"chat_{safe_session_id}.json"

    @staticmethod
    def _parse_messages(chat_dict: Dict[str, Any]) -> List[ChatMessage]:
        messages = []
        for msg in chat_dict.get("messages", []):
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            role = role if role in ("user", "assistant") else "user"
            content = msg.get("content")
            content = content if isinstance(content, str) else str(content or "")
            messages.append(ChatMessage(role=role, content=content))
        return messages

    def load_session_from_disk(self, session_id: str) -> bool:
        filepath = self._session_path(session_id)
        if not filepath.exists():
            return False
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                chat_dict = json.load(f)
            if not isinstance(chat_dict, dict):
                logger.warning("Chat file for session %s is not a JSON object", session_id[:12])
                return False
            self.sessions[session_id] = self._parse_messages(chat_dict)
            self._session_meta[session_id] = {
                "title": _clean_text(chat_dict.get("title"), HISTORY_TITLE_MAX_CHARS)
                or derive_title(self.sessions[session_id]),
                "created_at": chat_dict.get("created_at") or _ctime_iso(filepath),
                "title_is_custom": bool(chat_dict.get("title_is_custom")),
            }
            return True
        except Exception as e:
            logger.warning("Failed to load session %s from disk: %s", session_id[:12], e)
            return False

    def validate_session_id(self, session_id: str) -> bool:
        if not session_id or not session_id.strip():
            return False
        if "\0" in session_id:
            return False
        if ".." in session_id or "/" in session_id or "\\" in session_id:
            return False
        if len(session_id) > 255:
            return False
        return True

    def get_or_create_session(self, session_id: Optional[str] = None) -> str:
        if not session_id:
            new_session_id = str(uuid.uuid4())
            self.sessions[new_session_id] = []
            return new_session_id
        if not self.validate_session_id(session_id):
            raise ValueError(
                f"Invalid session_id format: {session_id}. Must be non-empty, "
                "without path traversal characters, and under 255 characters.")
        if session_id in self.sessions:
            return session_id
        if self.load_session_from_disk(session_id):
            return session_id
        self.sessions[session_id] = []
        return session_id

    def add_message(self, session_id: str, role: str, content: str):
        if session_id not in self.sessions:
            self.sessions[session_id] = []
        self.sessions[session_id].append(ChatMessage(role=role, content=content))

    def get_chat_history(self, session_id: str) -> List[ChatMessage]:
        return self.sessions.get(session_id, [])

    def format_history_for_llm(self, session_id: str, exclude_last: bool = False) -> List[tuple]:
        messages = self.get_chat_history(session_id)
        history = []
        messages_to_process = messages[:-1] if exclude_last and messages else messages
        i = 0
        while i < len(messages_to_process) - 1:
            user_msg = messages_to_process[i]
            ai_msg = messages_to_process[i + 1]
            if user_msg.role == "user" and ai_msg.role == "assistant":
                u_content = user_msg.content if isinstance(user_msg.content, str) else str(user_msg.content or "")
                a_content = ai_msg.content if isinstance(ai_msg.content, str) else str(ai_msg.content or "")
                history.append((u_content, a_content))
                i += 2
            else:
                i += 1
        if len(history) > MAX_CHAT_HISTORY_TURNS:
            history = history[-MAX_CHAT_HISTORY_TURNS:]
        return history

    # ===================== non-streaming chat ===================== #
    def process_message(self, session_id: str, user_message: str) -> str:
        logger.info("[GENERAL] Session: %s | User: %.200s", session_id[:12], user_message)
        self.add_message(session_id, "user", user_message)
        chat_history = self.format_history_for_llm(session_id, exclude_last=True)
        _, chat_idx = get_next_key_pair(len(GROQ_API_KEYS), need_brain=False)
        response = self.groq_service.get_response(question=user_message, chat_history=chat_history, key_start_index=chat_idx)
        self.add_message(session_id, "assistant", response)
        return response

    def process_realtime_message(self, session_id: str, user_message: str) -> str:
        if not self.realtime_service:
            raise ValueError("Realtime service is not initialized.")
        logger.info("[REALTIME] Session: %s | User: %.200s", session_id[:12], user_message)
        self.add_message(session_id, "user", user_message)
        chat_history = self.format_history_for_llm(session_id, exclude_last=True)
        _, chat_idx = get_next_key_pair(len(GROQ_API_KEYS), need_brain=False)
        response = self.realtime_service.get_response(question=user_message, chat_history=chat_history, key_start_index=chat_idx)
        self.add_message(session_id, "assistant", response)
        return response

    # ===================== streaming chat (plain) ===================== #
    def process_message_stream(
        self, session_id: str, user_message: str
    ) -> Iterator[Union[str, Dict[str, Any]]]:
        logger.info("[GENERAL-STREAM] Session: %s | User: %.200s", session_id[:12], user_message)
        self.add_message(session_id, "user", user_message)
        self.add_message(session_id, "assistant", "")
        chat_history = self.format_history_for_llm(session_id, exclude_last=True)

        yield {"_activity": {"event": "query_detected", "message": user_message}}
        yield {"_activity": {"event": "routing", "route": "general"}}
        yield {"_activity": {"event": "streaming_started", "route": "general"}}

        _, chat_idx = get_next_key_pair(len(GROQ_API_KEYS), need_brain=False)
        chunk_count = 0
        t0 = time.perf_counter()
        try:
            for chunk in self.groq_service.stream_response(
                question=user_message, chat_history=chat_history, key_start_index=chat_idx
            ):
                if isinstance(chunk, dict):
                    yield chunk
                    continue
                if chunk_count == 0:
                    yield {"_activity": {"event": "first_chunk", "route": "general", "elapsed_ms": int((time.perf_counter() - t0) * 1000)}}
                self.sessions[session_id][-1].content += chunk
                chunk_count += 1
                if chunk_count % SAVE_EVERY_N_CHUNKS == 0:
                    self.save_chat_session(session_id, log_timing=False)
                yield chunk
        finally:
            self.save_chat_session(session_id)

    def process_realtime_message_stream(
        self, session_id: str, user_message: str
    ) -> Iterator[Union[str, Dict[str, Any]]]:
        if not self.realtime_service:
            raise ValueError("Realtime service is not initialized.")
        logger.info("[REALTIME-STREAM] Session: %s | User: %.200s", session_id[:12], user_message)
        self.add_message(session_id, "user", user_message)
        self.add_message(session_id, "assistant", "")
        chat_history = self.format_history_for_llm(session_id, exclude_last=True)
        yield {"_activity": {"event": "query_detected", "message": user_message}}
        yield {"_activity": {"event": "routing", "route": "realtime"}}
        yield {"_activity": {"event": "streaming_started", "route": "realtime"}}
        _, chat_idx = get_next_key_pair(len(GROQ_API_KEYS), need_brain=False)
        chunk_count = 0
        t0 = time.perf_counter()
        try:
            for chunk in self.realtime_service.stream_response(
                question=user_message, chat_history=chat_history, key_start_index=chat_idx
            ):
                if isinstance(chunk, dict):
                    yield chunk
                    continue
                if chunk_count == 0:
                    yield {"_activity": {"event": "first_chunk", "route": "realtime", "elapsed_ms": int((time.perf_counter() - t0) * 1000)}}
                self.sessions[session_id][-1].content += chunk
                chunk_count += 1
                if chunk_count % SAVE_EVERY_N_CHUNKS == 0:
                    self.save_chat_session(session_id, log_timing=False)
                yield chunk
        finally:
            self.save_chat_session(session_id)

    # ===================== unified JARVIS route ===================== #
    def process_jarvis_message_stream(
        self, session_id: str, user_message: str, imgbase64: Optional[str] = None
    ) -> Iterator[Union[str, Dict[str, Any]]]:
        """Public JARVIS route.

        A thin wrapper around the implementation so passive memory extraction
        has exactly one hook point. The turn has ~30 exit paths (empty input,
        camera bypass, cache hit, confirmation prompt, planner, agent, error),
        and a `finally` on the generator covers all of them -- including the
        client disconnecting mid-stream, which still produced a real turn worth
        learning from.
        """
        try:
            yield from self._jarvis_stream_impl(session_id, user_message, imgbase64)
        finally:
            try:
                from app.services.memory_extractor import get_memory_extractor
                reply = ""
                try:
                    reply = self.sessions[session_id][-1].content or ""
                except Exception:  # noqa: BLE001 - session may be gone
                    pass
                get_memory_extractor().submit(user_message, reply)
            except Exception as _mxe:  # noqa: BLE001 - never affect the reply
                logger.debug("[MEMORY] extraction submit skipped: %s", _mxe)

    def _jarvis_stream_impl(
        self, session_id: str, user_message: str, imgbase64: Optional[str] = None
    ) -> Iterator[Union[str, Dict[str, Any]]]:
        t0_jarvis = time.perf_counter()
        turn_id = uuid.uuid4().hex
        dbg.session_start(session_id=session_id, user_message=user_message)
        dbg.info("TURN", "START", {
            "turn_id": turn_id[:12], "session_id": session_id[:12],
            "has_image": bool(imgbase64),
        })
        logger.info("[JARVIS-STREAM] Session: %s | User: %.200s | img: %s",
                    session_id[:12], user_message[:80], "yes" if imgbase64 else "no")
        self.add_message(session_id, "user", user_message)
        self.add_message(session_id, "assistant", "")
        chat_history = self.format_history_for_llm(session_id, exclude_last=True)

        # Phase 2 memory: cheaply capture obvious durable facts from this message
        # (e.g. "my name is...", "remember that..."). Fail-soft, no LLM, no latency.
        try:
            from app.services.memory_service import get_memory
            get_memory().auto_capture(user_message)
        except Exception:
            pass

        yield {"_activity": {"event": "query_detected", "message": user_message}}

        # ---- reject empty / noise input before it costs anything ----
        # Speech-to-text returns "." or "" for silence and background noise. That
        # used to be classified as a task and open a full agent execution with an
        # empty command, burning an LLM call on nothing.
        if not imgbase64 and len(str(user_message or "").strip(" .,!?-_\n\t\u0964")) < 2:
            text = "I didn't catch that. Could you say it again?"
            dbg.info("ROUTE", "EMPTY INPUT IGNORED", {"raw": repr(user_message)[:40]})
            self.sessions[session_id][-1].content = text
            yield text
            self.save_chat_session(session_id)
            dbg.session_end(final_response=text)
            return

        # ---- direct camera bypass (frontend attached an image) ----
        if imgbase64 and CAMERA_BYPASS_TOKEN in (user_message or ""):
            yield from self._handle_camera_with_image(session_id, user_message, imgbase64)
            dbg.session_end(final_response=self.sessions[session_id][-1].content)
            return

        _brain_idx, chat_idx = get_next_key_pair(len(GROQ_API_KEYS), need_brain=False)
        pending = self._pending_confirmations.get(session_id)

        # ================= M13 Phase 2: understand the turn =================
        # One call, before any routing decision. It replaces the old category
        # classifier AND the five hardcoded phrase lists that used to guess at
        # what the sentence meant.
        resolution = self._understand(session_id, user_message, chat_history, pending)
        primary_elapsed_ms = resolution.elapsed_ms
        dbg.understood(resolution.to_dict())
        try:
            from app.services.resolver import get_resolver
            resolver_ev = get_resolver().last_provider_event
        except Exception:  # noqa: BLE001
            resolver_ev = None
        if resolver_ev:
            yield {"_activity": resolver_ev}
        if resolution.understood:
            # §4.6 transparency: the owner sees what was understood EVERY turn,
            # which is the mitigation for a confidently-wrong resolution.
            yield {"_activity": {
                "event": "understood", "goal": resolution.goal,
                "kind": resolution.kind, "elapsed_ms": resolution.elapsed_ms,
                "self_contained": resolution.self_contained,
                "refers_to_previous": resolution.refers_to_previous,
                "message": "Understood: " + resolution.goal,
            }}
        # `decision.query_type` stays a ROUTE name, not the new `kind`. The
        # frontend drives orb states, route colours and the search starter sound
        # off this field, so changing its vocabulary would silently break all
        # three. The resolver's own vocabulary is carried alongside it.
        yield {"_activity": {"event": "decision",
                             "query_type": _ROUTE_FOR_KIND.get(resolution.kind,
                                                               "task"),
                             "kind": resolution.kind,
                             "reasoning": resolution.source.capitalize(),
                             "elapsed_ms": resolution.elapsed_ms}}

        # ---- no reasoning engine at all: replay or admit, never guess ----
        if not resolution.ok:
            handled = yield from self._try_cache_replay(
                session_id, user_message, t0_jarvis)
            if handled:
                return
            text = ("I can't reach my reasoning engine right now, so I won't guess "
                    "at what you meant. Try again in a moment.")
            dbg.info("ROUTE", "OFFLINE -- HONEST ERROR", {})
            logger.warning("[ROUTE] resolver offline and no verified cache entry")
            self.sessions[session_id][-1].content = text
            yield text
            self.save_chat_session(session_id)
            dbg.session_end(final_response=text)
            return

        # ---- confirmation reply for a pending dangerous action ----
        if pending:
            decision = resolution.is_confirmation
            if decision is True:
                self._pending_confirmations.pop(session_id, None)
                yield from self._run_confirmed_action(session_id, pending, t0_jarvis)
                return
            if decision is False:
                self._pending_confirmations.pop(session_id, None)
                dbg.info("CONFIRM", "CANCELLED BY USER", {"tool": pending.get("tool")})
                # Fall through: the same message may also carry a new request, and
                # the resolved goal already captures it.
            else:
                # Neither a yes nor a no. The pending action used to be dropped
                # silently here and the message fell through to conversation mode,
                # which then claimed the action had completed. Keep it and ask once.
                dbg.info("CONFIRM", "STILL PENDING", {
                    "tool": pending.get("tool"), "message": user_message[:120],
                })
                text = (f"I still need a yes before I run {pending.get('tool')} for "
                        f"\"{str(pending.get('original_message') or '').strip()[:80]}\". "
                        "Say yes to go ahead, or no to drop it.")
                self.sessions[session_id][-1].content = text
                yield text
                self.save_chat_session(session_id)
                dbg.session_end(final_response=text)
                return

        # ---- something is genuinely missing: ASK, never guess ----
        if resolution.needs_clarification:
            question = self._clarifying_question(resolution)
            dbg.info("ROUTE", "ASKING FOR CLARIFICATION",
                     {"unresolved": resolution.unresolved})
            logger.info("[ROUTE] unresolved %s -> asking", resolution.unresolved)
            yield {"_activity": {"event": "routing", "route": "clarify",
                                 "reasoning": "; ".join(resolution.unresolved)[:120]}}
            self.sessions[session_id][-1].content = question
            yield question
            self.save_chat_session(session_id)
            dbg.session_end(final_response=question)
            return

        kind = resolution.kind
        goal = resolution.goal or user_message

        # ---- state-based guard (§4.2) ----
        # Two independent things keep a failed action from being answered with
        # small talk: the resolver understanding the reference, AND this check,
        # which reads the recorded VERDICT rather than the user's words. It holds
        # even if the resolver misjudges the sentence.
        if kind not in ACTING_KINDS and resolution.refers_to_previous:
            last = self._last_goals.get(session_id) or {}
            if str(last.get("verdict") or "") in ("FAIL", "UNKNOWN"):
                dbg.info("ROUTE", "UNVERIFIED-ACTION GUARD", {
                    "from": kind, "last_verdict": last.get("verdict"),
                    "last_goal": str(last.get("goal") or "")[:120],
                })
                logger.info("[ROUTE] last action was %s and this refers back -> agent",
                            last.get("verdict"))
                kind = KIND_ACTION
                yield {"_activity": {"event": "routing", "route": "task",
                                     "reasoning": "the last action was not confirmed"}}

        # ---- camera (no image yet) ----
        if kind == KIND_VISUAL and resolution.visual_source == "camera":
            yield from self._handle_camera_route(session_id, goal, imgbase64)
            dbg.session_end(final_response=self.sessions[session_id][-1].content)
            return

        # ---- action / mixed / on-screen visual -> agent loop ----
        if kind in ACTING_KINDS:
            self._remember_goal(session_id, goal,
                                self_contained=resolution.self_contained)
            yield from self._run_agent(
                session_id, goal, chat_history, confirmed_tools=None,
                t0_jarvis=t0_jarvis, mixed=(kind == KIND_MIXED), chat_idx=chat_idx,
                self_contained=resolution.self_contained,
                original_message=user_message,
            )
            return

        # ---- questions -> stream a conversational answer ----
        use_realtime = kind == KIND_WEB_QUESTION and self.realtime_service
        route_name = "realtime" if use_realtime else "general"
        # A web search works far better on the RESOLVED goal ("who is Fahim
        # Abdullah") than on the raw pronoun ("who is he"). General chat already
        # has the full history, so its own wording is left untouched.
        question = goal if use_realtime else user_message
        yield {"_activity": {"event": "routing", "route": route_name}}
        dbg.info("ROUTE", "CONVERSATION STREAM", {"route": route_name})
        yield {"_activity": {"event": "streaming_started", "route": route_name}}
        stream_svc = self.realtime_service if use_realtime else self.groq_service
        chunk_count = 0
        ttfb_ms = -1
        t0 = time.perf_counter()
        try:
            for chunk in stream_svc.stream_response(
                question=question, chat_history=chat_history, key_start_index=chat_idx
            ):
                if isinstance(chunk, dict):
                    yield chunk
                    continue
                if chunk_count == 0:
                    ttfb_ms = int((time.perf_counter() - t0) * 1000)
                    yield {"_activity": {"event": "first_chunk", "route": route_name, "elapsed_ms": ttfb_ms}}
                self.sessions[session_id][-1].content += chunk
                chunk_count += 1
                if chunk_count % SAVE_EVERY_N_CHUNKS == 0:
                    self.save_chat_session(session_id, log_timing=False)
                yield chunk
        finally:
            self.save_chat_session(session_id)
            final_content = self.sessions[session_id][-1].content
            dbg.info("STREAM", "COMPLETED", {
                "route": route_name, "chunks": chunk_count,
                "characters": len(final_content),
            })
            dbg.session_end(final_response=final_content)
        # One-line timing breakdown so it's obvious where the wait was:
        # classify (brain) + first-token (model latency / search) + total.
        logger.info(
            "[TIMING] route=%s | classify=%dms | first-token=%dms | total=%.2fs | chunks=%d",
            route_name, primary_elapsed_ms, ttfb_ms,
            time.perf_counter() - t0_jarvis, chunk_count,
        )

    # ---- agent loop runner ----
    def _run_confirmed_action(self, session_id: str, pending: dict,
                              t0_jarvis: float) -> Iterator[Union[str, Dict[str, Any]]]:
        """Execute exactly the tool+arguments the user saw and approved.

        Never reruns the LLM after confirmation, so approval cannot silently
        drift to different arguments or authorize another action of the same
        tool type.
        """
        from app.services.agent import action_sink
        from app.services.agent.execution import ExecutionContext, get_execution_coordinator
        tool = str(pending.get("tool") or "")
        args = dict(pending.get("arguments") or {})
        execution_id = uuid.uuid4().hex
        coordinator = get_execution_coordinator()
        context = ExecutionContext(
            execution_id=execution_id, session_id=session_id,
            user_message=str(pending.get("original_message") or ""),
            source="confirmed_action",
        )
        action = coordinator.action(tool, args, index=1,
                                    action_id=str(pending.get("action_id") or uuid.uuid4().hex))
        yield {"_activity": {"event": "confirmation_granted", "tool": tool}}
        yield {"_activity": {"event": "execution_started", "execution_id": execution_id[:12]}}
        yield {"_activity": {"event": "tool_call", "tool": tool, "args": args, "step": 1}}
        action_sink.reset()
        manifest = coordinator.execute_plan(context, [action], confirmation_already_checked=True)
        result = manifest.results[0]
        yield {"_activity": {"event": "tool_result", "tool": tool,
                              "ok": result.transport_ok,
                              "preview": result.observation[:120]}}
        # The sink is drained per action inside the coordinator (M13 §3.2), so the
        # payload to emit is the one this result carries -- not whatever happens to
        # be sitting in the thread-local sink.
        if result.transport_ok and action_sink.bucket_has_actions(result.frontend_actions):
            actions = dict(_EMPTY_ACTIONS)
            actions.update(result.frontend_actions)
            yield {"_actions": actions}
        yield {"_activity": {"event": "verification_queued",
                              "execution_id": execution_id[:12], "actions": 1}}
        yield {"_activity": {"event": "agent_done", "steps": 1}}
        text = "Done." if result.transport_ok else "I couldn't complete the approved action."
        self.sessions[session_id][-1].content = text
        yield text
        self.save_chat_session(session_id)
        dbg.session_end(final_response=text)
        logger.info("[TIMING] route=task(confirmed) | total=%.2fs",
                    time.perf_counter() - t0_jarvis)

    def _run_agent(
        self,
        session_id: str,
        user_message: str,
        chat_history: List[tuple],
        confirmed_tools: Optional[List[str]],
        t0_jarvis: float,
        mixed: bool = False,
        chat_idx: int = 0,
        self_contained: bool = True,
        original_message: str = "",
    ) -> Iterator[Union[str, Dict[str, Any]]]:
        if not self.agent_loop:
            text = "Action handling isn't available right now."
            self.sessions[session_id][-1].content = text
            yield text
            self.save_chat_session(session_id)
            return

        # The turn log is opened by process_jarvis_message_stream for every route.
        dbg.info("CHAT_SERVICE", f"_run_agent called | session={session_id[:12]} | mixed={mixed} | confirmed={confirmed_tools}")

        # M13 §4.4: tell the cache whether this phrasing may EVER be promoted,
        # before anything runs. Only a command that carried its own meaning can
        # be replayed later; "close it" is permanently ineligible.
        try:
            from app.services.agent.cache.coordinator import get_phase6
            get_phase6().note_eligibility(user_message, self_contained)
            if original_message and original_message != user_message:
                get_phase6().note_eligibility(original_message, self_contained)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[CACHE] eligibility hint skipped: %s", exc)

        # M13 §4.3: the hand-written regex fast path is gone. Speed is now EARNED
        # -- a phrasing you use repeatedly, proven to work, replays from the
        # verified cache below. Nothing is fast because someone wrote a rule.
        if not mixed and not confirmed_tools:
            handled = yield from self._try_cache_replay(
                session_id, user_message, t0_jarvis)
            if handled:
                return
        yield {"_activity": {"event": "routing", "route": "mixed" if mixed else "task"}}

        final_text = ""
        actions_emitted = False
        executed: List[Dict[str, Any]] = []
        try:
            for event in self.agent_loop.run_stream(
                user_message, chat_history, key_index=chat_idx,
                confirmed_tools=confirmed_tools, expect_action=True,
            ):
                if "_executed" in event:
                    executed = list((event["_executed"] or {}).get("steps") or [])
                    self._remember_last_action(session_id, executed)
                    continue
                if "_confirm" in event:
                    confirm = event["_confirm"]
                    self._pending_confirmations[session_id] = {
                        "tool": confirm["tool"],
                        "arguments": dict(confirm.get("arguments") or {}),
                        "action_id": uuid.uuid4().hex,
                        "original_message": confirm["original_message"],
                    }
                    ask = (f"This will run a sensitive action ({confirm['tool']}). "
                           "Should I go ahead? Say yes to confirm.")
                    self.sessions[session_id][-1].content = ask
                    yield {"_activity": {"event": "awaiting_confirmation", "tool": confirm["tool"]}}
                    yield ask
                    self.save_chat_session(session_id)
                    dbg.session_end(final_response=ask)
                    logger.info("[JARVIS-STREAM] Awaiting confirmation for %s", confirm["tool"])
                    return
                if "_actions" in event:
                    actions = dict(_EMPTY_ACTIONS)
                    actions.update(event["_actions"])
                    yield {"_actions": actions}
                    actions_emitted = True
                    continue
                if "_activity" in event:
                    yield event
                    continue
                if "_final" in event:
                    final_text = event["_final"] or ""
                    continue

            # For mixed requests, append a conversational answer after the actions.
            if mixed and self.groq_service:
                # The action half of a mixed turn is verified too, BEFORE the
                # conversational half streams. Otherwise mixed would be the one
                # route where an action could silently fail while the reply reads
                # like everything went fine.
                caveat = ""
                if executed:
                    settled = yield from self._settle_before_reply(
                        session_id, user_message, chat_history,
                        final_text or "", executed, chat_idx=chat_idx)
                    if settled and settled != final_text:
                        caveat = settled.strip()
                yield {"_activity": {"event": "streaming_started", "route": "mixed"}}
                stream_svc = self.realtime_service if self.realtime_service else self.groq_service
                self.sessions[session_id][-1].content = ""
                if caveat:
                    lead = caveat + " "
                    self.sessions[session_id][-1].content = lead
                    yield lead
                for chunk in stream_svc.stream_response(
                    question=user_message, chat_history=chat_history, key_start_index=chat_idx
                ):
                    if isinstance(chunk, dict):
                        yield chunk
                        continue
                    self.sessions[session_id][-1].content += chunk
                    yield chunk
                self.save_chat_session(session_id)
            else:
                # ---- M13 §3.4: verify before speaking ----
                text = final_text or "Done."
                if executed:
                    settled = yield from self._settle_before_reply(
                        session_id, user_message, chat_history, text, executed,
                        chat_idx=chat_idx,
                    )
                    text = settled or text
                self.sessions[session_id][-1].content = text
                yield text
                self.save_chat_session(session_id)
        except Exception as e:
            logger.error("[JARVIS-STREAM] Agent loop error: %s", e, exc_info=True)
            text = "Something went wrong while performing that action."
            self.sessions[session_id][-1].content = text
            yield text
            self.save_chat_session(session_id)

        logger.info("[JARVIS-STREAM] Agent flow done in %.2fs | actions: %s",
                    time.perf_counter() - t0_jarvis, actions_emitted)

    # ===================== Phase 6: earned speed ===================== #
    def _try_cache_replay(
        self, session_id: str, command: str, t0_jarvis: float,
    ) -> Iterator[Union[str, Dict[str, Any]]]:
        """Replay a previously VERIFIED command, instantly and without an LLM.

        Only entries promoted after a Phase 4 PASS land here, and only for
        self-contained commands (M13 §4.4) -- "close it" can never be replayed.
        A miss falls straight through to the agent: reliability #1, speed #2.

        This is also the offline path: a verified replay needs no reasoning
        engine at all, so it keeps working when every LLM key is down.

        Returns True (via StopIteration.value) when it served the whole turn.
        """
        from app.services.agent import action_sink
        from app.services.agent.tool_registry import registry
        from app.services.agent.execution import (
            ExecutionContext, ExecutionManifest, get_execution_coordinator,
        )
        try:
            from app.services.agent.cache.coordinator import get_phase6
            phase6 = get_phase6()
            cached = phase6.lookup(command)
        except Exception:  # noqa: BLE001
            return False
        if not cached:
            yield {"_activity": {"event": "cache_miss"}}
            return False

        kind = cached.get("kind")
        payload = cached.get("payload") or {}
        coordinator = get_execution_coordinator()

        if kind == "response":
            say = payload.get("say")
            if not say:
                return False
            logger.info("[CACHE-HIT] %.60s -> static response", command)
            yield {"_activity": {"event": "routing", "route": "chat"}}
            yield {"_activity": {"event": "cache_hit", "kind": "response"}}
            self.sessions[session_id][-1].content = say
            yield say
            self.save_chat_session(session_id)
            dbg.session_end(final_response=say)
            logger.info("[TIMING] route=chat(cache) | total=%.2fs",
                        time.perf_counter() - t0_jarvis)
            return True

        # Normalise tool + plan entries into one list of steps.
        if kind == "tool":
            steps = [{"tool": payload.get("tool"), "args": payload.get("args") or {}}]
        elif kind == "plan":
            raw = payload.get("steps") or []
            steps = [s for s in raw if isinstance(s, dict) and s.get("tool")]
            if len(steps) != len(raw):
                return False
        else:
            return False
        if not steps or any(not s.get("tool") for s in steps):
            return False
        # Danger is re-checked at replay time: a tool can become dangerous after
        # an entry was stored, and the confirmation gate must never be bypassed.
        if any(registry.is_dangerous(s.get("tool")) for s in steps):
            return False

        execution_id = uuid.uuid4().hex
        context = ExecutionContext(
            execution_id=execution_id, session_id=session_id,
            user_message=command,
            source="cache_tool" if kind == "tool" else "cache_plan",
        )
        logger.info("[CACHE-HIT] %.60s -> %d step(s)", command, len(steps))
        yield {"_activity": {"event": "routing", "route": "task"}}
        yield {"_activity": {"event": "cache_hit", "kind": kind,
                             "tool": steps[0].get("tool"), "steps": len(steps)}}
        yield {"_activity": {"event": "cache_replay", "kind": kind,
                             "steps": len(steps)}}
        action_sink.reset()

        specs, results = [], []
        executed: List[Dict[str, Any]] = []
        replay_ok = True
        for index, step in enumerate(steps, start=1):
            tool = step.get("tool")
            args = step.get("args") or {}
            action_id = uuid.uuid4().hex
            yield {"_activity": {"event": "tool_call", "tool": tool,
                                 "args": args, "step": index}}
            spec = coordinator.action(tool, args, index=index, action_id=action_id)
            result = coordinator.execute_action(
                context, spec, confirmation_already_checked=True)
            specs.append(spec)
            results.append(result)
            executed.append({"action_id": action_id, "tool": tool, "args": dict(args)})
            replay_ok = replay_ok and result.transport_ok
            yield {"_activity": {"event": "tool_result", "tool": tool,
                                 "ok": result.transport_ok,
                                 "preview": str(result.observation)[:120]}}
            if result.transport_ok and action_sink.bucket_has_actions(
                    result.frontend_actions):
                actions = dict(_EMPTY_ACTIONS)
                actions.update(result.frontend_actions)
                yield {"_actions": actions}
            if not result.transport_ok:
                break

        coordinator.complete(ExecutionManifest(
            context=context, actions=specs, results=results,
            status="completed" if replay_ok else "failed",
        ))
        yield {"_activity": {"event": "verification_queued",
                             "execution_id": execution_id[:12],
                             "actions": len(executed)}}
        yield {"_activity": {"event": "agent_done", "steps": len(executed)}}
        if not replay_ok:
            try:
                phase6.invalidate(command)
            except Exception:  # noqa: BLE001
                pass

        say = payload.get("say") or self._reply_from_results(results, replay_ok)
        # A replay is verified like any other execution, so it gets the same
        # verify-before-reply treatment: a stale cache entry cannot report success.
        if replay_ok and executed:
            settled = yield from self._settle_before_reply(
                session_id, command, [], say, executed, chat_idx=0)
            say = settled or say
        self.sessions[session_id][-1].content = say
        yield say
        self.save_chat_session(session_id)
        dbg.session_end(final_response=say)
        logger.info("[TIMING] route=task(cache) | total=%.2fs",
                    time.perf_counter() - t0_jarvis)
        return True

    @staticmethod
    def _reply_from_results(results: List[Any], ok: bool) -> str:
        """A spoken line for a cache replay, from what the tools actually said.

        A promoted entry stores the tool and its arguments, not a sentence -- the
        verdict that promotes it arrives before the reply is composed. Falling back
        to "Done." made every earned-speed hit sound like a shrug, so use the
        tool's own observation, which is already written as a short human sentence
        ("Opening https://example.com in the browser.", "Battery: 100%").
        """
        if not ok:
            return "I couldn't complete that this time."
        for result in reversed(list(results or [])):
            observation = str(getattr(result, "observation", "") or "").strip()
            if (observation and not observation.startswith("ERROR")
                    and len(observation) <= 200 and "\n" not in observation):
                return observation
        return "Done."

    # ===================== M13 §3.4: verify before speaking ===================== #
    @staticmethod
    def _phase4_ready():
        """The Phase 4 coordinator, but only when it can actually produce verdicts."""
        try:
            from app.services.agent.checker import get_phase4
            p4 = get_phase4()
            return p4 if getattr(p4, "started", False) else None
        except Exception:  # noqa: BLE001
            return None

    def _await_verdicts(self, executed: List[Dict[str, Any]],
                        budget: float) -> Iterator[Union[str, Dict[str, Any]]]:
        """Wait, within one shared budget, for the verdicts of this turn's actions.

        Yields progress activity so the UI is never silent, then returns the list
        of verdict dicts through StopIteration.value. A missing verdict is
        reported as UNKNOWN -- never assumed to be a success.
        """
        p4 = self._phase4_ready()
        verdicts: List[Dict[str, Any]] = []
        if p4 is None or not executed:
            return verdicts
        wait_fn = getattr(p4, "wait_for_verdict", None)
        if not callable(wait_fn):
            return verdicts
        yield {"_activity": {"event": "verifying", "actions": len(executed),
                             "message": "checking it actually worked"}}
        deadline = time.perf_counter() + max(0.0, float(budget))
        for step in executed:
            action_id = str(step.get("action_id") or "")
            tool = str(step.get("tool") or "")
            remaining = deadline - time.perf_counter()
            payload = None
            if action_id and remaining > 0:
                try:
                    payload = wait_fn(action_id, remaining)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("[VERIFY] wait failed for %s: %s", tool, exc)
                    payload = None
            if payload is None:
                verdicts.append({"tool": tool, "action_id": action_id,
                                 "verdict": "UNKNOWN",
                                 "reason": "verification did not come back in time"})
            else:
                verdicts.append({
                    "tool": payload.get("tool") or tool,
                    "action_id": action_id,
                    "verdict": str(payload.get("verdict") or "UNKNOWN"),
                    "reason": str(payload.get("reason") or ""),
                    "evidence": str(payload.get("evidence") or ""),
                })
            yield {"_activity": {"event": "verdict", "tool": verdicts[-1]["tool"],
                                 "verdict": verdicts[-1]["verdict"],
                                 "reason": verdicts[-1].get("reason", "")[:120]}}
        return verdicts

    @staticmethod
    def _retry_is_safe(executed: List[Dict[str, Any]]) -> bool:
        """Only re-run a turn whose every action is safe to repeat.

        Reuses the Learner's own risk classification so there is exactly one
        definition of "safe to do twice" in the codebase.
        """
        if not executed:
            return False
        try:
            from app.services.agent.checker.learner import retry_is_safe
        except Exception:  # noqa: BLE001
            return False
        return all(retry_is_safe(str(s.get("tool") or "")) for s in executed)

    def _settle_before_reply(
        self,
        session_id: str,
        user_message: str,
        chat_history: List[tuple],
        draft: str,
        executed: List[Dict[str, Any]],
        chat_idx: int = 0,
        retry_round: int = 0,
    ) -> Iterator[Union[str, Dict[str, Any]]]:
        """Turn a draft reply into a true one.

        PASS everywhere  -> the draft stands.
        FAIL             -> re-enter the agent loop once with the reason, then
                            re-verify. Still failing -> say so.
        UNKNOWN / FAIL   -> the reply states the limit of what was confirmed.

        Returns the text to say (through StopIteration.value). Never raises; on
        any internal problem the draft is returned unchanged so the turn still
        completes.
        """
        import config as _cfg
        if not bool(getattr(_cfg, "VERIFY_BEFORE_REPLY", True)):
            return draft
        budget = float(getattr(_cfg, "VERIFY_WAIT_TIMEOUT", 3.0))
        try:
            verdicts = yield from self._await_verdicts(executed, budget)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[VERIFY] settle failed: %s", exc)
            return draft
        if not verdicts:
            return draft

        self._record_verdicts(session_id, verdicts)
        failed = [v for v in verdicts if v.get("verdict") == "FAIL"]
        unknown = [v for v in verdicts if v.get("verdict") == "UNKNOWN"]

        # ---- one retry on a definitive failure ----
        if (failed and not retry_round
                and bool(getattr(_cfg, "AGENT_RETRY_ON_FAIL", True))
                and self._retry_is_safe(executed)):
            reason = "; ".join(
                r for r in ((v.get("reason") or "") for v in failed) if r)[:300]
            dbg.info("VERIFY", "RETRYING AFTER FAIL", {"reason": reason[:160]})
            logger.info("[VERIFY] FAIL -> one retry | %s", reason[:120])
            yield {"_activity": {"event": "retrying", "reason": reason[:120],
                                 "message": "that didn't take effect, trying again"}}
            retry_goal = (f"{user_message}\n\n[The previous attempt was verified as "
                          f"NOT having worked: {reason}. Do it again, differently if "
                          "needed, and confirm the effect from the tool results.]")
            draft2, executed2 = yield from self._agent_pass(
                retry_goal, chat_history, chat_idx)
            if executed2:
                verdicts2 = yield from self._await_verdicts(executed2, budget)
                self._record_verdicts(session_id, verdicts2)
                if verdicts2 and all(v.get("verdict") == "PASS" for v in verdicts2):
                    return draft2 or draft
                verdicts = verdicts2 or verdicts
                draft = draft2 or draft
                failed = [v for v in verdicts if v.get("verdict") == "FAIL"]
                unknown = [v for v in verdicts if v.get("verdict") == "UNKNOWN"]
            else:
                draft = draft2 or draft

        if not failed and not unknown:
            return draft  # everything PASSed: the draft is true, say it as-is.

        # ---- admit the limit, in wording the agent composes itself ----
        try:
            honest = self.agent_loop.compose_unconfirmed(
                user_message, draft, failed + unknown)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[VERIFY] honest wording failed: %s", exc)
            honest = ""
        return honest or draft

    def _agent_pass(self, goal: str, chat_history: List[tuple],
                    chat_idx: int) -> Iterator[Union[str, Dict[str, Any]]]:
        """Run one agent-loop pass, forwarding its events.

        Returns (final_text, executed_steps) through StopIteration.value. Used by
        the retry path so a retry does not recurse through the whole routing
        stack (cache lookups, fast paths, goal bookkeeping) a second time.
        """
        final_text = ""
        executed: List[Dict[str, Any]] = []
        for event in self.agent_loop.run_stream(
            goal, chat_history, key_index=chat_idx, expect_action=True,
        ):
            if "_executed" in event:
                executed = list((event["_executed"] or {}).get("steps") or [])
                continue
            if "_final" in event:
                final_text = event["_final"] or ""
                continue
            if "_confirm" in event:
                # A retry should only ever touch idempotent, non-dangerous tools,
                # so this is not expected. Stop rather than silently escalate.
                logger.warning("[VERIFY] retry asked for confirmation -- stopping")
                break
            if "_actions" in event:
                actions = dict(_EMPTY_ACTIONS)
                actions.update(event["_actions"])
                yield {"_actions": actions}
                continue
            yield event
        return final_text, executed

    def _remember_last_action(self, session_id: str,
                              executed: List[Dict[str, Any]]) -> None:
        """Keep the last action this session really performed, for the resolver."""
        if not executed:
            return
        last = executed[-1]
        self._last_actions[session_id] = {
            "tool": str(last.get("tool") or ""),
            "args": dict(last.get("args") or {}),
            "verdict": "", "reason": "", "at": time.time(),
        }

    def _record_verdicts(self, session_id: str,
                         verdicts: List[Dict[str, Any]]) -> None:
        """Remember how this turn's actions really ended.

        The stored verdict is what lets the next turn route on FACT rather than
        on the words the user chose: if the last action did not verify and the
        user's next message refers back to it, the conversational route is not
        allowed to answer it (M13 §4.2).
        """
        if not verdicts:
            return
        worst = "PASS"
        for v in verdicts:
            verdict = str(v.get("verdict") or "UNKNOWN")
            if verdict == "FAIL":
                worst = "FAIL"
                break
            if verdict != "PASS":
                worst = "UNKNOWN"
        goal = self._last_goals.get(session_id)
        if goal is None:
            goal = {"goal": "", "at": time.time()}
            self._last_goals[session_id] = goal
        reason = "; ".join(
            r for r in ((str(v.get("reason") or "")) for v in verdicts) if r)[:300]
        goal["verdict"] = worst
        goal["verdict_reason"] = reason
        goal["verdict_at"] = time.time()
        action = self._last_actions.get(session_id)
        if action is not None:
            action["verdict"] = worst
            action["reason"] = reason

    # ---- camera handlers ----
    def _handle_camera_with_image(
        self, session_id: str, user_message: str, imgbase64: str
    ) -> Iterator[Union[str, Dict[str, Any]]]:
        yield {"_activity": {"event": "decision", "query_type": "camera", "reasoning": "Image attached", "elapsed_ms": 0}}
        yield {"_activity": {"event": "routing", "route": "vision"}}
        yield {"_activity": {"event": "vision_analyzing", "message": "Analyzing image..."}}
        yield {"_activity": {"event": "streaming_started", "route": "vision"}}
        prompt = (user_message or "").replace(CAMERA_BYPASS_TOKEN, "").strip() or "What do you see in this image?"
        if len(self.sessions[session_id]) >= 2:
            self.sessions[session_id][-2].content = prompt
        _save_camera_image(imgbase64, session_id)
        if self.vision_service:
            text = self.vision_service.describe_image(imgbase64, prompt)
        else:
            text = "Vision is not available. Please set GROQ_API_KEY."
        self.sessions[session_id][-1].content = text
        yield text
        self.save_chat_session(session_id)

    def _handle_camera_route(
        self, session_id: str, user_message: str, imgbase64: Optional[str]
    ) -> Iterator[Union[str, Dict[str, Any]]]:
        yield {"_activity": {"event": "routing", "route": "camera"}}
        if imgbase64:
            yield {"_activity": {"event": "vision_analyzing", "message": "Analyzing image..."}}
            yield {"_activity": {"event": "streaming_started", "route": "vision"}}
            _save_camera_image(imgbase64, session_id)
            if self.vision_service:
                text = self.vision_service.describe_image(imgbase64, user_message)
            else:
                text = "Vision is not available. Please set GROQ_API_KEY."
        else:
            text = "Let me take a look..."
            actions = dict(_EMPTY_ACTIONS)
            actions["cam"] = {"action": "open_and_capture", "resend_message": user_message}
            yield {"_actions": actions}
            yield {"_activity": {"event": "actions_emitted", "message": "camera (auto-capture)"}}
        self.sessions[session_id][-1].content = text
        yield text
        self.save_chat_session(session_id)

    def _remember_goal(self, session_id: str, goal: str,
                       self_contained: bool = True) -> None:
        """Remember the last real request, its phrasing quality and its verdict.

        No phrase filtering happens here any more: the resolver has already turned
        the utterance into a self-contained goal, so what lands here is always the
        real request rather than "it's not playing".
        """
        text = (goal or "").strip()
        if not text:
            return
        self._last_goals[session_id] = {
            "goal": text, "at": time.time(), "verdict": "",
            "self_contained": bool(self_contained),
        }

    # ===================== M13 Phase 2: understanding ===================== #
    def _understand(self, session_id: str, user_message: str,
                    chat_history: List[tuple],
                    pending: Optional[Dict[str, Any]] = None):
        """Run the resolver for this turn. Never raises.

        Gathers exactly the context the plan specifies: the utterance, the recent
        conversation (user AND assistant text), the live state block, the last
        action of this session together with its VERDICT, and relevant memory.
        """
        from app.services.resolver import Resolution, get_resolver
        try:
            resolver = get_resolver()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[RESOLVER] unavailable: %s", exc)
            return Resolution(goal=user_message, source="offline")

        state_block = ""
        try:
            if self.agent_loop is not None:
                state_block = self.agent_loop._build_state_block(
                    user_message, chat_history) or ""
        except Exception as exc:  # noqa: BLE001
            logger.debug("[RESOLVER] state block unavailable: %s", exc)

        memory_facts = ""
        try:
            from app.services.memory_service import get_memory
            memory_facts = get_memory().recall(user_message, limit=6) or ""
        except Exception as exc:  # noqa: BLE001
            logger.debug("[RESOLVER] memory recall unavailable: %s", exc)

        try:
            return resolver.resolve(
                user_message, chat_history=chat_history, state_block=state_block,
                last_action=self._last_actions.get(session_id),
                memory_facts=memory_facts, confirmation_pending=pending,
            )
        except Exception as exc:  # noqa: BLE001 - understanding must never crash a turn
            logger.warning("[RESOLVER] resolve failed: %s", exc)
            return Resolution(goal=user_message, source="offline")

    def _clarifying_question(self, resolution) -> str:
        """Ask for the one missing detail, in the agent's own words.

        Generated rather than templated so it reads like a question and not like
        a form validation error. The fallback is plain but still honest.
        """
        missing = "; ".join(resolution.unresolved)[:200]
        fallback = (f"Before I do that I need one thing: {missing}. "
                    "Could you tell me?")
        if self.agent_loop is None:
            return fallback
        try:
            return self.agent_loop._compose(
                "The owner asked: \"" + str(resolution.goal)[:250] + "\"\n"
                f"You cannot act yet because this is missing: {missing}.\n"
                "Ask one short, natural question to get exactly that. Do not "
                "apologise, do not claim to have started anything.",
                fallback,
            )
        except Exception:  # noqa: BLE001
            return fallback

    # ===================== startup briefing ===================== #
    def process_startup_brief_stream(self, session_id: str) -> Iterator[Union[str, Dict[str, Any]]]:
        dbg.session_start(session_id=session_id, user_message="(daily startup brief)")
        dbg.info("STARTUP_BRIEF", "START", {"session_id": session_id[:12]})
        # The brief's "user message" is an internal prompt, not something the user
        # typed. Marking the session transient keeps it off disk entirely, so it
        # can never surface in the history sidebar or become a conversation title.
        self._transient_sessions.add(session_id)
        if not self.realtime_service:
            dbg.error("STARTUP_BRIEF", "Realtime service is not initialized")
            dbg.session_end(final_response="")
            raise ValueError("Realtime service is not initialized.")
        logger.info("[STARTUP-STREAM] Session: %s", session_id[:12])
        # ---- Daily cache: generate the brief once per day, then replay it ----
        today = time.strftime("%Y-%m-%d")
        cached = getattr(self, "_startup_brief_cache", None)
        if cached and cached.get("date") == today and cached.get("text"):
            cached_text = cached["text"]
            logger.info("[STARTUP-STREAM] Serving cached brief for %s (no LLM/weather calls)", today)
            self.add_message(session_id, "user", "(daily startup brief \u2014 cached)")
            self.add_message(session_id, "assistant", cached_text)
            yield {"_activity": {"event": "routing", "route": "startup"}}
            yield {"_activity": {"event": "streaming_started", "route": "startup"}}
            for i in range(0, len(cached_text), 80):
                yield cached_text[i:i + 80]
            self.save_chat_session(session_id)
            dbg.info("STARTUP_BRIEF", "CACHE HIT", {
                "date": today, "characters": len(cached_text),
            })
            dbg.session_end(final_response=cached_text)
            logger.info("[STARTUP-STREAM] Completed (cached) | chars: %d", len(cached_text))
            return
        email_line = self._get_startup_email_line()
        calendar_line = self._get_startup_calendar_line()
        # Deterministic greeting -> its TTS is always a cache hit (same text all day),
        # so it rides the ONE voice cache like every other line -- no special handling.
        _hr = int(time.strftime("%H"))
        greeting = (
            "Good morning, Ayush." if _hr < 12
            else "Good afternoon, Ayush." if _hr < 17
            else "Good evening, Ayush."
        )
        prompt = (
            "Please search the current weather and give me a 6-sentence startup briefing in English.\n"
            f"Line 1: Say exactly this greeting: {greeting}\n"
            "Line 2: Today is [Day], [Date].\n"
            "Line 3: Short weather summary for morning, afternoon, evening.\n"
            "Line 4: One sentence of advice based on weather.\n"
            f"Line 5: Say exactly this email update: {email_line}\n"
            f"Line 6: Say exactly this calendar update: {calendar_line}\n"
            "Output ONLY the 6 sentences, no introductory or concluding text. English only."
        )
        self.add_message(session_id, "user", prompt)
        self.add_message(session_id, "assistant", "")
        yield {"_activity": {"event": "routing", "route": "startup"}}
        yield {"_activity": {"event": "streaming_started", "route": "startup"}}
        _, chat_idx = get_next_key_pair(len(GROQ_API_KEYS), need_brain=False)
        chunk_count = 0
        try:
            for chunk in self.realtime_service.stream_response(
                question=prompt, chat_history=[], key_start_index=chat_idx
            ):
                if isinstance(chunk, dict):
                    yield chunk
                    continue
                self.sessions[session_id][-1].content += chunk
                chunk_count += 1
                yield chunk
        except Exception as e:
            dbg.error("STARTUP_BRIEF", "Generation failed", {
                "error": str(e), "chunks_received": chunk_count,
            })
            raise
        finally:
            # Cache the freshly generated brief for the rest of the day.
            if chunk_count > 0:
                self._startup_brief_cache = {
                    "date": today,
                    "text": self.sessions[session_id][-1].content,
                }
            self.save_chat_session(session_id)
            final_brief = self.sessions[session_id][-1].content
            dbg.info("STARTUP_BRIEF", "FINISH", {
                "chunks": chunk_count, "characters": len(final_brief),
                "saved_empty": not bool(final_brief.strip()),
            })
            dbg.session_end(final_response=final_brief)
            logger.info("[STARTUP-STREAM] Completed | Chunks: %d", chunk_count)

    # ===================== persistence ===================== #
    def save_chat_session(self, session_id: str, log_timing: bool = True):
        if session_id in self._transient_sessions:
            # Never persisted, so it can never show up in the history sidebar.
            # Guarding here (rather than at each call site) also covers the
            # save-all-sessions pass on shutdown.
            return
        if session_id not in self.sessions or not self.sessions[session_id]:
            return
        messages = self.sessions[session_id]
        filepath = self._session_path(session_id)
        meta = self._load_meta_for_save(session_id, filepath, messages)
        chat_dict = {
            "schema_version": CHAT_SCHEMA_VERSION,
            "session_id": session_id,
            "title": meta["title"],
            "title_is_custom": meta["title_is_custom"],
            "created_at": meta["created_at"],
            "updated_at": _now_iso(),
            "message_count": len(messages),
            "messages": [{"role": msg.role, "content": msg.content} for msg in messages],
        }
        max_retries = 3
        last_exc = None
        for attempt in range(max_retries):
            try:
                with self._save_lock:
                    self._atomic_write_json(filepath, chat_dict)
                    return
            except OSError as e:
                last_exc = e
                if attempt < max_retries - 1:
                    time.sleep(0.1 * (attempt + 1))
            except Exception as e:
                logger.error("Failed to save chat session %s: %s", session_id, e)
                return
        logger.error("Failed to save chat session %s after %d retries: %s", session_id, max_retries, last_exc)

    @staticmethod
    def _atomic_write_json(filepath: Path, payload: Dict[str, Any]) -> None:
        """Write to a temp file in the same directory, then os.replace() it in.
        An interrupted write can no longer leave a half-written chat file."""
        tmp = filepath.with_name(filepath.name + f".tmp{os.getpid()}")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, filepath)
        except Exception:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise

    def _load_meta_for_save(
        self, session_id: str, filepath: Path, messages: List[ChatMessage]
    ) -> Dict[str, Any]:
        """Resolve title/created_at for a save, preserving a user rename.

        Uses the in-memory cache first; falls back to reading the existing file
        once (legacy chats and post-restart continues) and caches the result.
        """
        meta = self._session_meta.get(session_id)
        if meta is None:
            meta = {"title": "", "created_at": "", "title_is_custom": False}
            if filepath.exists():
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        existing = json.load(f)
                    if isinstance(existing, dict):
                        meta["title"] = _clean_text(existing.get("title"), HISTORY_TITLE_MAX_CHARS)
                        meta["created_at"] = existing.get("created_at") or ""
                        meta["title_is_custom"] = bool(existing.get("title_is_custom"))
                except Exception as e:
                    logger.warning("Could not read metadata for %s: %s", session_id[:12], e)
            if not meta["created_at"]:
                meta["created_at"] = _ctime_iso(filepath) if filepath.exists() else _now_iso()
            self._session_meta[session_id] = meta
        # A derived title tracks the first user message until the user renames.
        if not meta["title_is_custom"]:
            meta["title"] = derive_title(messages)
        elif not meta["title"]:
            meta["title"] = derive_title(messages)
        return meta

    # ===================== conversation history ===================== #
    def _summary_from_dict(self, chat_dict: Dict[str, Any], filepath: Path) -> Optional[Dict[str, Any]]:
        """Build a list summary. session_id comes from the file contents, never
        the filename (the filename strips dashes and is not reversible)."""
        session_id = chat_dict.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            return None
        messages = chat_dict.get("messages")
        if not isinstance(messages, list) or not messages:
            return None
        parsed = self._parse_messages(chat_dict)
        if not parsed:
            return None
        title = _clean_text(chat_dict.get("title"), HISTORY_TITLE_MAX_CHARS) or derive_title(parsed)
        return {
            "session_id": session_id,
            "title": title,
            "preview": _clean_text(parsed[-1].content, HISTORY_PREVIEW_MAX_CHARS),
            "created_at": chat_dict.get("created_at") or _ctime_iso(filepath),
            "updated_at": chat_dict.get("updated_at") or _mtime_iso(filepath),
            "message_count": len(parsed),
        }

    def _iter_chat_files(self):
        try:
            return sorted(CHATS_DATA_DIR.glob("chat_*.json"))
        except OSError as e:
            logger.warning("Could not scan chat directory: %s", e)
            return []

    def list_conversations(
        self, query: str = "", limit: int = 0, cursor: Optional[str] = None
    ) -> Dict[str, Any]:
        """Newest-first summaries with optional search and cursor pagination."""
        limit = limit or HISTORY_PAGE_SIZE
        limit = max(1, min(limit, HISTORY_MAX_PAGE_SIZE))
        needle = (query or "").strip().lower()

        summaries: List[Dict[str, Any]] = []
        skipped = 0
        for filepath in self._iter_chat_files():
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    chat_dict = json.load(f)
                if not isinstance(chat_dict, dict):
                    skipped += 1
                    continue
                summary = self._summary_from_dict(chat_dict, filepath)
                if summary is None:
                    skipped += 1
                    continue
                if needle and not self._matches(chat_dict, summary, needle):
                    continue
                summaries.append(summary)
            except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
                # One damaged chat must never break the whole sidebar.
                skipped += 1
                logger.warning("Skipping unreadable chat file %s: %s", filepath.name, type(e).__name__)

        # Stable newest-first ordering on (updated_at, session_id).
        summaries.sort(key=lambda s: (s["updated_at"], s["session_id"]), reverse=True)
        if skipped:
            logger.info("[HISTORY] Skipped %d unreadable/empty chat file(s)", skipped)

        start = 0
        if cursor:
            for idx, item in enumerate(summaries):
                if self._cursor_of(item) == cursor:
                    start = idx + 1
                    break
        page = summaries[start:start + limit]
        remaining = summaries[start + limit:]
        return {
            "conversations": page,
            "next_cursor": self._cursor_of(page[-1]) if page and remaining else None,
            "total": len(summaries),
        }

    @staticmethod
    def _cursor_of(summary: Dict[str, Any]) -> str:
        return f"{summary['updated_at']}|{summary['session_id']}"

    @staticmethod
    def _matches(chat_dict: Dict[str, Any], summary: Dict[str, Any], needle: str) -> bool:
        if needle in summary["title"].lower():
            return True
        for msg in chat_dict.get("messages", []):
            if isinstance(msg, dict) and needle in str(msg.get("content") or "").lower():
                return True
        return False

    def get_conversation(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Full conversation with metadata. Memory first, then disk. None when
        the conversation does not exist anywhere."""
        if session_id in self._transient_sessions:
            # Not a conversation -- it only ever lived in memory to drive a stream.
            return None
        filepath = self._session_path(session_id)
        chat_dict: Optional[Dict[str, Any]] = None
        if filepath.exists():
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    chat_dict = loaded
            except Exception as e:
                logger.warning("Could not read conversation %s: %s", session_id[:12], e)

        messages = self.sessions.get(session_id)
        if messages is None and chat_dict is None:
            return None
        if messages is None:
            messages = self._parse_messages(chat_dict)
        if not messages:
            return None

        chat_dict = chat_dict or {}
        title = _clean_text(chat_dict.get("title"), HISTORY_TITLE_MAX_CHARS) or derive_title(messages)
        return {
            "session_id": session_id,
            "title": title,
            "created_at": chat_dict.get("created_at") or (_ctime_iso(filepath) if filepath.exists() else _now_iso()),
            "updated_at": chat_dict.get("updated_at") or (_mtime_iso(filepath) if filepath.exists() else _now_iso()),
            "message_count": len(messages),
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }

    def rename_conversation(self, session_id: str, title: str) -> Optional[Dict[str, Any]]:
        """Persist a user-chosen title. Returns the updated summary, or None if
        the conversation has no file on disk yet."""
        clean = _clean_text(title, HISTORY_TITLE_MAX_CHARS)
        if not clean:
            raise ValueError("Title must not be empty")
        filepath = self._session_path(session_id)
        if not filepath.exists():
            return None
        with self._save_lock:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    chat_dict = json.load(f)
                if not isinstance(chat_dict, dict):
                    return None
            except Exception as e:
                logger.warning("Rename failed to read %s: %s", session_id[:12], e)
                return None
            chat_dict["schema_version"] = CHAT_SCHEMA_VERSION
            chat_dict["title"] = clean
            chat_dict["title_is_custom"] = True
            chat_dict.setdefault("created_at", _ctime_iso(filepath))
            chat_dict.setdefault("updated_at", _mtime_iso(filepath))
            self._atomic_write_json(filepath, chat_dict)
            meta = self._session_meta.setdefault(
                session_id,
                {"title": clean, "created_at": chat_dict["created_at"], "title_is_custom": True},
            )
            meta["title"] = clean
            meta["title_is_custom"] = True
            return self._summary_from_dict(chat_dict, filepath)

    def delete_conversation(self, session_id: str) -> bool:
        """Permanently delete the conversation file and drop all in-memory state
        for it. Returns False when there was nothing to delete."""
        filepath = self._session_path(session_id)
        existed = filepath.exists() or session_id in self.sessions
        if not existed:
            return False
        with self._save_lock:
            if filepath.exists():
                try:
                    filepath.unlink()
                except OSError as e:
                    logger.error("Failed to delete conversation %s: %s", session_id[:12], e)
                    raise
            self.sessions.pop(session_id, None)
            self._session_meta.pop(session_id, None)
            self._pending_confirmations.pop(session_id, None)
            self._last_goals.pop(session_id, None)
        logger.info("[HISTORY] Deleted conversation %s", session_id[:12])
        return True
