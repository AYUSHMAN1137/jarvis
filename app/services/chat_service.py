import base64
import json
import logging
import time
from pathlib import Path
from typing import List, Optional, Dict, Iterator, Any, Union
import uuid
import threading

from config import CHATS_DATA_DIR, CAMERA_CAPTURES_DIR, MAX_CHAT_HISTORY_TURNS, GROQ_API_KEYS
from app.models import ChatMessage
from app.services.groq_service import GroqService
from app.services.realtime_service import RealtimeGroqService
from app.services.brain_service import BrainService
from app.services.vision_service import VisionService
from app.services.agent.agent_loop import AgentLoop
from app.utils.key_rotation import get_next_key_pair
from app.services.debug_logger import dbg

logger = logging.getLogger("J.A.R.V.I.S")

CATEGORY_GENERAL = "general"
CATEGORY_REALTIME = "realtime"
CATEGORY_CAMERA = "camera"
CATEGORY_TASK = "task"
CATEGORY_MIXED = "mixed"

CAMERA_BYPASS_TOKEN = "TTCAMTOKENTT"
SAVE_EVERY_N_CHUNKS = 5

# Empty frontend-actions skeleton.
_EMPTY_ACTIONS = {
    "wopens": [], "plays": [], "images": [], "contents": [],
    "googlesearches": [], "youtubesearches": [], "cam": None,
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
        brain_service: BrainService = None,
        vision_service: VisionService = None,
        agent_loop: AgentLoop = None,
    ):
        self.groq_service = groq_service
        self.realtime_service = realtime_service
        self.brain_service = brain_service
        self.vision_service = vision_service
        self.agent_loop = agent_loop
        self.sessions: Dict[str, List[ChatMessage]] = {}
        self._save_lock = threading.Lock()
        # session_id -> {"tool": str, "original_message": str} for dangerous-action confirms
        self._pending_confirmations: Dict[str, Dict[str, Any]] = {}

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
    def load_session_from_disk(self, session_id: str) -> bool:
        safe_session_id = session_id.replace("-", "").replace(" ", "_")
        filepath = CHATS_DATA_DIR / f"chat_{safe_session_id}.json"
        if not filepath.exists():
            return False
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                chat_dict = json.load(f)
            messages = []
            for msg in chat_dict.get("messages", []):
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role")
                role = role if role in ("user", "assistant") else "user"
                content = msg.get("content")
                content = content if isinstance(content, str) else str(content or "")
                messages.append(ChatMessage(role=role, content=content))
            self.sessions[session_id] = messages
            return True
        except Exception as e:
            logger.warning("Failed to load session %s from disk: %s", session_id, e)
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

        # ---- direct camera bypass (frontend attached an image) ----
        if imgbase64 and CAMERA_BYPASS_TOKEN in (user_message or ""):
            yield from self._handle_camera_with_image(session_id, user_message, imgbase64)
            dbg.session_end(final_response=self.sessions[session_id][-1].content)
            return

        # ---- confirmation reply for a pending dangerous action ----
        pending = self._pending_confirmations.get(session_id)
        if pending and self._is_affirmative(user_message):
            self._pending_confirmations.pop(session_id, None)
            yield from self._run_confirmed_action(session_id, pending, t0_jarvis)
            return
        elif pending:
            # Any non-affirmative reply cancels the pending action.
            self._pending_confirmations.pop(session_id, None)

        # ---- classify ----
        brain_idx, chat_idx = get_next_key_pair(len(GROQ_API_KEYS), need_brain=bool(self.brain_service))
        category, primary_method, primary_elapsed_ms = CATEGORY_GENERAL, "default", 0
        if self.brain_service:
            category, primary_method, primary_elapsed_ms = self.brain_service.classify_primary(
                user_message, chat_history, key_index=brain_idx if brain_idx is not None else 0
            )
        dbg.info("ROUTE", "CLASSIFIED", {
            "turn_id": turn_id[:12], "category": category,
            "method": primary_method, "elapsed_ms": primary_elapsed_ms,
        })
        yield {"_activity": {"event": "decision", "query_type": category,
                             "reasoning": primary_method.capitalize(), "elapsed_ms": primary_elapsed_ms}}

        # Surface which provider/key the brain used to decide the route.
        brain_ev = getattr(self.brain_service, "last_provider_event", None) if self.brain_service else None
        if brain_ev:
            yield {"_activity": brain_ev}

        # ---- camera (no image yet) ----
        if category == CATEGORY_CAMERA:
            yield from self._handle_camera_route(session_id, user_message, imgbase64)
            dbg.session_end(final_response=self.sessions[session_id][-1].content)
            return

        # ---- task / mixed -> agent loop ----
        if category in (CATEGORY_TASK, CATEGORY_MIXED):
            yield from self._run_agent(session_id, user_message, chat_history,
                                       confirmed_tools=None, t0_jarvis=t0_jarvis,
                                       mixed=(category == CATEGORY_MIXED), chat_idx=chat_idx)
            return

        # ---- general / realtime -> stream conversational answer ----
        use_realtime = category == CATEGORY_REALTIME and self.realtime_service
        route_name = "realtime" if use_realtime else "general"
        yield {"_activity": {"event": "routing", "route": route_name}}
        dbg.info("ROUTE", "CONVERSATION STREAM", {"route": route_name})
        yield {"_activity": {"event": "streaming_started", "route": route_name}}
        stream_svc = self.realtime_service if use_realtime else self.groq_service
        chunk_count = 0
        ttfb_ms = -1
        t0 = time.perf_counter()
        try:
            for chunk in stream_svc.stream_response(
                question=user_message, chat_history=chat_history, key_start_index=chat_idx
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
        if result.transport_ok and action_sink.has_actions():
            actions = dict(_EMPTY_ACTIONS)
            actions.update(action_sink.collect())
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
    ) -> Iterator[Union[str, Dict[str, Any]]]:
        if not self.agent_loop:
            text = "Action handling isn't available right now."
            self.sessions[session_id][-1].content = text
            yield text
            self.save_chat_session(session_id)
            return

        # The turn log is opened by process_jarvis_message_stream for every route.
        dbg.info("CHAT_SERVICE", f"_run_agent called | session={session_id[:12]} | mixed={mixed} | confirmed={confirmed_tools}")

        # ---- deterministic fast-path for explicit web commands ----
        # LLM tool-calling is unreliable for simple mechanical commands, so handle
        # the unambiguous ones (open <site> / play <x> / search <x>) directly:
        # instant and 100%% reliable. Everything else falls through to the agent.
        if not mixed and not confirmed_tools:
            from app.services.agent.tools.web_tools import try_direct_web_command
            from app.services.agent import action_sink
            from app.services.agent.tool_registry import registry
            cmd = try_direct_web_command(user_message)
            if cmd:
                execution_id = uuid.uuid4().hex
                logger.info("[FAST-PATH] %.60s -> %s(%s)", user_message, cmd["tool"], cmd["args"])
                dbg.fast_path(matched=True, tool=cmd["tool"], args=cmd["args"])
                dbg.execution_event("started", execution_id, {
                    "path": "direct", "tool": cmd["tool"],
                })
                yield {"_activity": {"event": "routing", "route": "task"}}
                yield {"_activity": {"event": "fast_path", "tool": cmd["tool"]}}
                yield {"_activity": {"event": "execution_started",
                                      "execution_id": execution_id[:12]}}
                yield {"_activity": {"event": "tool_call", "tool": cmd["tool"], "args": cmd["args"], "step": 1}}
                action_sink.reset()
                from app.services.agent.execution import ExecutionContext, get_execution_coordinator
                direct_coordinator = get_execution_coordinator()
                direct_context = ExecutionContext(
                    execution_id=execution_id, session_id=session_id,
                    user_message=user_message, source="direct",
                )
                direct_manifest = direct_coordinator.execute_plan(
                    direct_context,
                    [direct_coordinator.action(cmd["tool"], cmd["args"], index=1)],
                    confirmation_already_checked=True,
                )
                direct_result = direct_manifest.results[0]
                observation = direct_result.observation
                ok = direct_result.transport_ok
                yield {"_activity": {"event": "tool_result", "tool": cmd["tool"], "ok": ok, "preview": str(observation)[:120]}}
                if ok and action_sink.has_actions():
                    actions = dict(_EMPTY_ACTIONS)
                    actions.update(action_sink.collect())
                    yield {"_actions": actions}
                    yield {"_activity": {"event": "actions_emitted",
                                          "message": "Browser action sent"}}
                    dbg.info("FRONTEND", "ACTIONS EMITTED", {
                        "tool": cmd["tool"], "has_actions": True,
                    })
                dbg.execution_event("completed", execution_id, {
                    "path": "direct", "tool": cmd["tool"], "ok": ok,
                })
                yield {"_activity": {"event": "execution_completed",
                                      "execution_id": execution_id[:12], "ok": ok,
                                      "path": "direct"}}
                yield {"_activity": {"event": "agent_done", "steps": 1}}
                say = cmd["say"] if ok else "Sorry Sir, I couldn't do that."
                self.sessions[session_id][-1].content = say
                yield say
                self.save_chat_session(session_id)
                dbg.session_end(final_response=say)
                logger.info("[TIMING] route=task(fast) | total=%.2fs", time.perf_counter() - t0_jarvis)
                return

        # ---- Phase 6: verified-command cache (instant replay) ----
        # Only exact, non-referential, non-dangerous commands that were
        # previously VERIFIED (Phase 4 Checker PASS) land here. A miss falls
        # straight through to the normal agent path -- reliability #1, speed #2.
        # Replays are re-published as action.done so the Checker keeps verifying
        # them and auto-evicts the entry on a later FAIL (self-healing cache).
        if not mixed and not confirmed_tools:
            cached = None
            try:
                from app.services.agent.cache.coordinator import get_phase6
                _p6 = get_phase6()
                cached = _p6.lookup(user_message)
            except Exception:
                cached = None
            if cached is None:
                yield {"_activity": {"event": "cache_miss"}}
            if cached and cached.get("kind") == "tool":
                from app.services.agent import action_sink
                from app.services.agent.tool_registry import registry
                cpay = cached.get("payload") or {}
                ctool = cpay.get("tool")
                cargs = cpay.get("args") or {}
                if ctool and not registry.is_dangerous(ctool):
                    logger.info("[CACHE-HIT] %.60s -> %s(%s)", user_message, ctool, cargs)
                    yield {"_activity": {"event": "routing", "route": "task"}}
                    yield {"_activity": {"event": "cache_hit", "tool": ctool}}
                    yield {"_activity": {"event": "cache_replay", "kind": "tool",
                                          "steps": 1}}
                    yield {"_activity": {"event": "tool_call", "tool": ctool, "args": cargs, "step": 1}}
                    action_sink.reset()
                    from app.services.agent.execution import ExecutionContext, get_execution_coordinator
                    replay_execution_id = uuid.uuid4().hex
                    replay_coordinator = get_execution_coordinator()
                    replay_context = ExecutionContext(
                        execution_id=replay_execution_id, session_id=session_id,
                        user_message=user_message, source="cache_tool",
                    )
                    replay_manifest = replay_coordinator.execute_plan(
                        replay_context,
                        [replay_coordinator.action(ctool, cargs, index=1)],
                        confirmation_already_checked=True,
                    )
                    replay_result = replay_manifest.results[0]
                    observation = replay_result.observation
                    ok = replay_result.transport_ok
                    yield {"_activity": {"event": "verification_queued",
                                          "execution_id": replay_execution_id[:12],
                                          "actions": 1}}
                    yield {"_activity": {"event": "tool_result", "tool": ctool, "ok": ok, "preview": str(observation)[:120]}}
                    if ok and action_sink.has_actions():
                        actions = dict(_EMPTY_ACTIONS)
                        actions.update(action_sink.collect())
                        yield {"_actions": actions}
                    yield {"_activity": {"event": "agent_done", "steps": 1}}
                    if not ok:
                        try:
                            _p6.invalidate(user_message)
                        except Exception:
                            pass
                    say = cpay.get("say") or ("Done." if ok else "Sorry Sir, I couldn't do that.")
                    self.sessions[session_id][-1].content = say
                    yield say
                    self.save_chat_session(session_id)
                    dbg.session_end(final_response=say)
                    logger.info("[TIMING] route=task(cache) | total=%.2fs", time.perf_counter() - t0_jarvis)
                    return
            elif cached and cached.get("kind") == "plan":
                from app.services.agent import action_sink
                from app.services.agent.tool_registry import registry
                steps = (cached.get("payload") or {}).get("steps") or []
                safe_steps = [s for s in steps if isinstance(s, dict) and s.get("tool")]
                if (safe_steps and len(safe_steps) == len(steps)
                        and not any(registry.is_dangerous(s.get("tool")) for s in safe_steps)):
                    replay_execution_id = uuid.uuid4().hex
                    replay_manifest = []
                    replay_ok = True
                    action_sink.reset()
                    from app.services.agent.execution import (
                        ExecutionContext, ExecutionManifest, get_execution_coordinator,
                    )
                    plan_coordinator = get_execution_coordinator()
                    plan_context = ExecutionContext(
                        execution_id=replay_execution_id, session_id=session_id,
                        user_message=user_message, source="cache_plan",
                    )
                    plan_actions = []
                    plan_results = []
                    yield {"_activity": {"event": "routing", "route": "task"}}
                    yield {"_activity": {"event": "cache_hit", "kind": "plan",
                                          "steps": len(safe_steps)}}
                    yield {"_activity": {"event": "cache_replay", "kind": "plan",
                                          "steps": len(safe_steps)}}
                    for index, cached_step in enumerate(safe_steps, start=1):
                        ctool = cached_step.get("tool")
                        cargs = cached_step.get("args") or {}
                        action_id = uuid.uuid4().hex
                        replay_manifest.append({"action_id": action_id, "tool": ctool, "args": cargs})
                        yield {"_activity": {"event": "tool_call", "tool": ctool,
                                              "args": cargs, "step": index}}
                        action_spec = plan_coordinator.action(
                            ctool, cargs, index=index, action_id=action_id
                        )
                        action_result = plan_coordinator.execute_action(
                            plan_context, action_spec, confirmation_already_checked=True
                        )
                        plan_actions.append(action_spec)
                        plan_results.append(action_result)
                        observation = action_result.observation
                        step_ok = action_result.transport_ok
                        replay_ok = replay_ok and step_ok
                        yield {"_activity": {"event": "tool_result", "tool": ctool,
                                              "ok": step_ok, "preview": str(observation)[:120]}}
                        if not step_ok:
                            break
                    plan_coordinator.complete(ExecutionManifest(
                        context=plan_context, actions=plan_actions, results=plan_results,
                        status="completed" if replay_ok else "failed",
                    ))
                    yield {"_activity": {"event": "verification_queued",
                                          "execution_id": replay_execution_id[:12],
                                          "actions": len(replay_manifest)}}
                    if replay_ok and action_sink.has_actions():
                        actions = dict(_EMPTY_ACTIONS)
                        actions.update(action_sink.collect())
                        yield {"_actions": actions}
                    if not replay_ok:
                        try:
                            _p6.invalidate(user_message)
                        except Exception:
                            pass
                    yield {"_activity": {"event": "agent_done",
                                          "steps": len(replay_manifest)}}
                    say = ((cached.get("payload") or {}).get("say")
                           or ("Done." if replay_ok else "I couldn't complete the cached workflow."))
                    self.sessions[session_id][-1].content = say
                    yield say
                    self.save_chat_session(session_id)
                    dbg.session_end(final_response=say)
                    logger.info("[TIMING] route=task(cache-plan) | total=%.2fs",
                                time.perf_counter() - t0_jarvis)
                    return
            elif cached and cached.get("kind") == "response":
                say = (cached.get("payload") or {}).get("say")
                if say:
                    logger.info("[CACHE-HIT] %.60s -> static response", user_message)
                    yield {"_activity": {"event": "routing", "route": "chat"}}
                    yield {"_activity": {"event": "cache_hit", "kind": "response"}}
                    self.sessions[session_id][-1].content = say
                    yield say
                    self.save_chat_session(session_id)
                    dbg.session_end(final_response=say)
                    logger.info("[TIMING] route=chat(cache) | total=%.2fs", time.perf_counter() - t0_jarvis)
                    return

        yield {"_activity": {"event": "routing", "route": "mixed" if mixed else "task"}}

        final_text = ""
        actions_emitted = False
        try:
            for event in self.agent_loop.run_stream(
                user_message, chat_history, key_index=chat_idx, confirmed_tools=confirmed_tools
            ):
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
                yield {"_activity": {"event": "streaming_started", "route": "mixed"}}
                stream_svc = self.realtime_service if self.realtime_service else self.groq_service
                self.sessions[session_id][-1].content = ""
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
                text = final_text or "Done."
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

    @staticmethod
    def _is_affirmative(message: str) -> bool:
        m = (message or "").strip().lower()
        return m in (
            "yes", "y", "yeah", "yep", "yup", "sure", "ok", "okay", "go ahead",
            "do it", "confirm", "confirmed", "haan", "haa", "ha", "kar do", "karo",
            "yes please", "go", "proceed",
        ) or m.startswith(("yes", "haan", "go ahead", "do it", "confirm"))

    # ===================== startup briefing ===================== #
    def process_startup_brief_stream(self, session_id: str) -> Iterator[Union[str, Dict[str, Any]]]:
        dbg.session_start(session_id=session_id, user_message="(daily startup brief)")
        dbg.info("STARTUP_BRIEF", "START", {"session_id": session_id[:12]})
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
        if session_id not in self.sessions or not self.sessions[session_id]:
            return
        messages = self.sessions[session_id]
        safe_session_id = session_id.replace("-", "").replace(" ", "_")
        filepath = CHATS_DATA_DIR / f"chat_{safe_session_id}.json"
        chat_dict = {
            "session_id": session_id,
            "messages": [{"role": msg.role, "content": msg.content} for msg in messages],
        }
        max_retries = 3
        last_exc = None
        for attempt in range(max_retries):
            try:
                with self._save_lock:
                    with open(filepath, "w", encoding="utf-8") as f:
                        json.dump(chat_dict, f, indent=2, ensure_ascii=False)
                    return
            except OSError as e:
                last_exc = e
                if attempt < max_retries - 1:
                    time.sleep(0.1 * (attempt + 1))
            except Exception as e:
                logger.error("Failed to save chat session %s: %s", session_id, e)
                return
        logger.error("Failed to save chat session %s after %d retries: %s", session_id, max_retries, last_exc)
