"""Application lifespan: startup and shutdown of all services.

Extracted from the old monolithic main.py. Initialises Phases 1-8,
the watcher, memory, embedding, LLM services, agent loop, and chat service.
"""

import time
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

import app.core.state as state
from app.core.streaming import tts_pool

from app.services.vector_store import VectorStoreService
from app.services.groq_service import GroqService
from app.services.realtime_service import RealtimeGroqService
from app.services.chat_service import ChatService
from app.services.resolver import get_resolver
from app.services.vision_service import VisionService
from app.services.agent.agent_loop import AgentLoop
from app.services.agent.tools import load_all_tools
from app.services.agent import deps as agent_deps
from app.services.google.gmail import GmailService
from app.services.google.calendar import CalendarService
from app.services.google.drive import DriveService

from config import (
    GROQ_API_KEYS, GROQ_MODEL, SERPER_API_KEY,
    EMBEDDING_MODEL, CHUNK_SIZE, CHUNK_OVERLAP, MAX_CHAT_HISTORY_TURNS,
    ASSISTANT_NAME,
)

logger = logging.getLogger("J.A.R.V.I.S")


def print_title():
    title = """

   ╔══════════════════════════════════════════════════════════╗
   ║                                                          ║
   ║         ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗          ║
   ║         ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝          ║
   ║         ██║███████║██████╔╝██║   ██║██║███████╗          ║
   ║    ██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║          ║
   ║    ╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║          ║
   ║     ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝          ║
   ║                                                          ║
   ║          Just A Rather Very Intelligent System           ║
   ║                                                          ║
   ╚══════════════════════════════════════════════════════════╝

    """
    try:
        print(title)
    except UnicodeEncodeError:
        print("=== J.A.R.V.I.S - Just A Rather Very Intelligent System ===")


@asynccontextmanager
async def lifespan(app: FastAPI):
    print_title()
    logger.info("=" * 60)
    logger.info("J.A.R.V.I.S - Starting Up...")
    logger.info("=" * 60)
    logger.info("[CONFIG] Assistant name: %s", ASSISTANT_NAME)
    logger.info("[CONFIG] Groq model: %s", GROQ_MODEL)
    logger.info("[CONFIG] Groq API keys loaded: %d", len(GROQ_API_KEYS))
    logger.info("[CONFIG] Serper API key: %s", "configured" if SERPER_API_KEY else "NOT SET")
    logger.info("[CONFIG] Image generation: Pollinations.ai (free, no API key)")
    logger.info("[CONFIG] Embedding model: %s", EMBEDDING_MODEL)
    logger.info("[CONFIG] Chunk size: %d | Overlap: %d | Max history turns: %d",
                CHUNK_SIZE, CHUNK_OVERLAP, MAX_CHAT_HISTORY_TURNS)

    try:
        _t_start = time.perf_counter()
        from app.services import llm_providers

        # Start the background System State Watcher as early as possible so it
        # begins tracking processes/windows before the first command arrives.
        # Non-fatal: if it can't start, JARVIS keeps working the old way.
        try:
            from app.services.watcher import get_watcher
            get_watcher().start()
            logger.info("[STARTUP] System state watcher (background daemon) started.")
        except Exception as _we:  # noqa: BLE001
            logger.warning("[STARTUP] Watcher could not start (non-fatal): %s", _we)

        # Phase 2: warm the persistent memory store (creates the SQLite DB and
        # the editable profile files). Non-fatal -- memory is always fail-soft.
        try:
            from app.services.memory_service import get_memory
            logger.info("[STARTUP] Persistent memory ready: %s", get_memory().status())
        except Exception as _me:  # noqa: BLE001
            logger.warning("[STARTUP] Memory store init issue (non-fatal): %s", _me)

        _t = time.perf_counter()
        logger.info("[STARTUP] Loading embedding model '%s' on CPU (first run downloads it; usually the slowest step)...", EMBEDDING_MODEL)
        state.vector_store_service = VectorStoreService()
        logger.info("[STARTUP] 1/5 Embedding model loaded in %.2fs", time.perf_counter() - _t)

        _t = time.perf_counter()
        state.vector_store_service.create_vector_store()
        logger.info("[STARTUP] 2/5 Vector index built in %.2fs", time.perf_counter() - _t)

        _t = time.perf_counter()
        state.groq_service = GroqService(state.vector_store_service)
        state.realtime_service = RealtimeGroqService(state.vector_store_service)
        state.resolver = get_resolver()
        state.vision_service = VisionService()
        logger.info("[STARTUP] 3/5 LLM services (Groq + Realtime + Resolver + Vision) ready in %.2fs", time.perf_counter() - _t)

        _t = time.perf_counter()
        agent_deps.configure(
            groq_service=state.groq_service,
            gmail_service=GmailService(),
            calendar_service=CalendarService(),
            drive_service=DriveService(),
        )
        load_all_tools()
        from app.services.agent.tool_registry import registry as _tool_registry
        state.agent_loop = AgentLoop()
        logger.info("[STARTUP] 4/5 Agent loop + %d tools ready in %.2fs", len(_tool_registry.names()), time.perf_counter() - _t)

        # Phase 4: start Checker + Vision + Skill-maker + Learner (background,
        # fail-soft). PHASE4_ENABLED=False makes JARVIS behave like Phase 3.
        try:
            from app.services.agent.checker import get_phase4
            get_phase4().start()
        except Exception as _p4e:  # noqa: BLE001
            logger.warning("[STARTUP] Phase 4 could not start (non-fatal): %s", _p4e)

        # Phase 5: start the Planner/Executor coordinator (reuses Phase 4's bus +
        # checker, fail-soft). PHASE5_ENABLED=False makes JARVIS behave like P4.
        try:
            from app.services.agent.planner import get_phase5
            get_phase5().start()
        except Exception as _p5e:  # noqa: BLE001
            logger.warning("[STARTUP] Phase 5 could not start (non-fatal): %s", _p5e)

        # UI automation: start the dedicated COM apartment and build the UIA
        # backend now. Importing pywinauto and creating the UIA Desktop costs a
        # few seconds once; paying it here means the first UI command the user
        # gives is fast instead of looking like a hang. Fail-soft: if this does
        # not come up, the ui_* tools report honestly that they are unavailable.
        try:
            from app.services.agent.automation import get_uia_engine
            from app.services.agent.automation.ui_thread import get_ui_thread
            get_ui_thread().start()
            _warm = get_uia_engine().warmup()
            logger.info("[STARTUP] UI automation %s.",
                        "ready" if _warm.get("ok") else "unavailable")
        except Exception as _uiae:  # noqa: BLE001
            logger.warning("[STARTUP] UI automation warmup skipped (non-fatal): %s", _uiae)

        # Phase 6: start the verified-command cache (promote on Checker PASS,
        # evict on FAIL, fail-soft). PHASE6_ENABLED=False makes JARVIS behave
        # exactly like Phase 5 (every command goes through the normal path).
        try:
            from app.services.agent.cache import get_phase6
            get_phase6().start()
        except Exception as _p6e:  # noqa: BLE001
            logger.warning("[STARTUP] Phase 6 could not start (non-fatal): %s", _p6e)

        # Phase 7: start the proactive engine (suggest-only by default; reacts
        # to watcher events via the bus, fail-soft). PHASE7_ENABLED=False or
        # PROACTIVE_AUTO_ACT=False keeps JARVIS from ever acting on its own.
        try:
            from app.services.agent.proactive import get_phase7
            get_phase7().start()
        except Exception as _p7e:  # noqa: BLE001
            logger.warning("[STARTUP] Phase 7 could not start (non-fatal): %s", _p7e)

        # Phase 8: start the user model (facts + aliases + learned habits),
        # aggregate habits from the action log once at boot, then feed those
        # habits to Phase 7 so proactive suggestions are personalized.
        try:
            from app.services.agent.personalization import get_phase8
            _p8 = get_phase8()
            _p8.start()
            try:
                _p8.aggregate_from_provider()
            except Exception as _p8a:  # noqa: BLE001
                logger.debug("[STARTUP] Phase 8 habit aggregation skipped: %s", _p8a)
            try:
                from app.services.agent.proactive import get_phase7 as _gp7
                _gp7().set_habit_provider(_p8.habits_for)
            except Exception as _p8w:  # noqa: BLE001
                logger.debug("[STARTUP] Phase 8->7 wiring skipped: %s", _p8w)
        except Exception as _p8e:  # noqa: BLE001
            logger.warning("[STARTUP] Phase 8 could not start (non-fatal): %s", _p8e)

        # File index for find_file. Built on a background thread so it never
        # delays boot, and refreshed lazily on search -- deliberately not a
        # second poller.
        try:
            from app.services.agent.file_index import get_file_index
            _index = get_file_index()
            if _index.enabled and _index.is_stale():
                _index.build_async()
                logger.info("[STARTUP] File index refreshing in the background.")
            else:
                logger.info("[STARTUP] File index ready (%d files).", _index.count())
        except Exception as _fie:  # noqa: BLE001
            logger.warning("[STARTUP] File index skipped (non-fatal): %s", _fie)

        # Housekeeping: prune re-creatable artefacts and snapshot the databases.
        # Runs after every phase is up so all five databases exist, and before
        # the chat service so it can never collide with a live request.
        try:
            from app.services.maintenance import run_startup_maintenance
            run_startup_maintenance()
        except Exception as _mte:  # noqa: BLE001
            logger.warning("[STARTUP] Maintenance skipped (non-fatal): %s", _mte)

        # M8: Reminder scheduler — start the heapq-based background thread
        # that fires reminders when they're due. Fail-soft.
        try:
            from app.services.reminder_service import get_reminder_service
            from app.api.reminders import on_reminder_fire
            _reminder_svc = get_reminder_service()
            _reminder_svc.recover_stuck()  # re-fire anything stuck from a crash
            _reminder_svc.start(on_fire=on_reminder_fire)
            logger.info("[STARTUP] Reminder scheduler started (%d active).",
                        len(_reminder_svc._heap))
        except Exception as _rse:  # noqa: BLE001
            logger.warning("[STARTUP] Reminder scheduler could not start (non-fatal): %s", _rse)

        # Notes & To-Do service — warm up the database.
        try:
            from app.services.notes_service import get_notes_service
            _notes_svc = get_notes_service()
            _stats = _notes_svc.get_stats()
            logger.info("[STARTUP] Notes service ready (%d notes, %d to-do lists).",
                        _stats['notes'], _stats['todo_lists'])
        except Exception as _nse:  # noqa: BLE001
            logger.warning("[STARTUP] Notes service could not start (non-fatal): %s", _nse)

        _t = time.perf_counter()
        state.chat_service = ChatService(
            state.groq_service, state.realtime_service,
            vision_service=state.vision_service,
            agent_loop=state.agent_loop,
        )
        logger.info("[STARTUP] 5/5 Chat service ready in %.2fs", time.perf_counter() - _t)

        logger.info("=" * 60)
        logger.info(
            "J.A.R.V.I.S ONLINE in %.2fs total  |  %d tools  |  %d Groq keys  |  Gemini: %s  |  Serper: %s",
            time.perf_counter() - _t_start,
            len(_tool_registry.names()),
            len(GROQ_API_KEYS),
            "ON" if llm_providers.gemini_enabled() else "OFF",
            "ON" if SERPER_API_KEY else "OFF",
        )
        logger.info("Frontend: http://localhost:8000/jarvis/   |   API: http://localhost:8000")
        logger.info("=" * 60)

        yield

        logger.info("\nShutting down J.A.R.V.I.S...")
        tts_pool.shutdown(wait=True)

        # Stop the reminder scheduler before closing databases
        try:
            from app.services.reminder_service import get_reminder_service
            get_reminder_service().stop()
        except Exception as _rse:  # noqa: BLE001
            logger.debug("[SHUTDOWN] Reminder scheduler stop error: %s", _rse)

        try:
            from app.services.watcher import get_watcher
            get_watcher().stop()
        except Exception as _we:  # noqa: BLE001
            logger.debug("[SHUTDOWN] Watcher stop error: %s", _we)

        try:
            from app.services.agent.personalization import get_phase8
            get_phase8().stop()
        except Exception as _p8e:  # noqa: BLE001
            logger.debug("[SHUTDOWN] Phase 8 stop error: %s", _p8e)

        try:
            from app.services.agent.proactive import get_phase7
            get_phase7().stop()
        except Exception as _p7e:  # noqa: BLE001
            logger.debug("[SHUTDOWN] Phase 7 stop error: %s", _p7e)

        try:
            from app.services.agent.cache import get_phase6
            get_phase6().stop()
        except Exception as _p6e:  # noqa: BLE001
            logger.debug("[SHUTDOWN] Phase 6 stop error: %s", _p6e)

        try:
            from app.services.agent.planner import get_phase5
            get_phase5().stop()
        except Exception as _p5e:  # noqa: BLE001
            logger.debug("[SHUTDOWN] Phase 5 stop error: %s", _p5e)

        try:
            from app.services.agent.checker import get_phase4
            get_phase4().stop()
        except Exception as _p4e:  # noqa: BLE001
            logger.debug("[SHUTDOWN] Phase 4 stop error: %s", _p4e)

        if state.chat_service:
            for session_id in list(state.chat_service.sessions.keys()):
                state.chat_service.save_chat_session(session_id)

        # Last step, after every phase has stopped writing: merge each WAL back
        # into its database and close the connection. Without this SQLite leaves
        # the -wal/-shm files behind, the log grows forever, and every later read
        # has to scan it. Must run after the phases stop, or a background thread
        # can reopen a connection we just closed.
        try:
            from app.services.db import checkpoint_and_close_all
            checkpoint_and_close_all()
        except Exception as _dbe:  # noqa: BLE001
            logger.debug("[SHUTDOWN] Database checkpoint error: %s", _dbe)

        logger.info("All sessions saved. Goodbye!")

    except Exception as e:
        logger.error(f"Fatal error during startup: {e}", exc_info=True)
        raise
