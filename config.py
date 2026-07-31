import os
import logging
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

LEARNING_DATA_DIR = BASE_DIR / "data" / "learning_data"
CHATS_DATA_DIR = BASE_DIR / "data" / "chats_data"
VECTOR_STORE_DIR = BASE_DIR / "data" / "vector_store"
CAMERA_CAPTURES_DIR = BASE_DIR / "data" / "camera_captures"
VOICE_CACHE_DIR = BASE_DIR / "data" / "voice_cache"
GOOGLE_CREDENTIALS_PATH = Path(os.getenv("GOOGLE_CREDENTIALS_FILE", str(BASE_DIR / "credentials.json")))
GOOGLE_TOKEN_PATH = Path(os.getenv("GOOGLE_TOKEN_FILE", str(BASE_DIR / "data" / "google_token.json")))
LEGACY_GMAIL_TOKEN_PATH = BASE_DIR / "data" / "gmail_token.json"
GMAIL_CREDENTIALS_PATH = GOOGLE_CREDENTIALS_PATH
GMAIL_TOKEN_PATH = GOOGLE_TOKEN_PATH
# gmail.send is additive to readonly, and sending is the one thing the assistant
# could never do (it could read your inbox but not reply).
# NOTE: changing this list invalidates an existing data/google_token.json --
# delete that file and re-run OAuth once.
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]
GOOGLE_CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar"]
GOOGLE_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
GOOGLE_SCOPES = list(dict.fromkeys(GMAIL_SCOPES + GOOGLE_CALENDAR_SCOPES + GOOGLE_DRIVE_SCOPES))

LEARNING_DATA_DIR.mkdir(parents=True, exist_ok=True)
CHATS_DATA_DIR.mkdir(parents=True, exist_ok=True)
VECTOR_STORE_DIR.mkdir(parents=True, exist_ok=True)
CAMERA_CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
VOICE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

def _load_groq_api_keys() -> list:
    keys = []

    first = os.getenv("GROQ_API_KEY", "").strip()
    if first:
        keys.append(first)

    i = 2

    while True:
        k = os.getenv(f"GROQ_API_KEY_{i}", "").strip()

        if not k:
            break

        keys.append(k)
        i += 1

    return keys

GROQ_API_KEYS = _load_groq_api_keys()
GROQ_API_KEY = GROQ_API_KEYS[0] if GROQ_API_KEYS else ""
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
def _load_serper_api_keys() -> list:
    keys = []
    first = os.getenv("SERPER_API_KEY", "").strip()
    if first:
        keys.append(first)
    i = 2
    while True:
        k = os.getenv(f"SERPER_API_KEY_{i}", "").strip()
        if not k:
            break
        keys.append(k)
        i += 1
    return keys

SERPER_API_KEYS = _load_serper_api_keys()
SERPER_API_KEY = SERPER_API_KEYS[0] if SERPER_API_KEYS else ""
GROQ_BRAIN_MODEL = os.getenv("GROQ_BRAIN_MODEL", "openai/gpt-oss-20b")
INTENT_CLASSIFY_MODEL = os.getenv("INTENT_CLASSIFY_MODEL", "openai/gpt-oss-20b")
# The brain classifier must fail fast and fall back to rule-based routing.
# Keep this small so a slow / rate-limited API call can't hang the whole reply.
BRAIN_CLASSIFY_TIMEOUT = int(os.getenv("BRAIN_CLASSIFY_TIMEOUT", "4"))
TASK_EXECUTION_TIMEOUT = int(os.getenv("TASK_EXECUTION_TIMEOUT", "30"))

# === Agentic tool-calling (desktop + web automation) ===
# A capable model is used so tool selection is reliable. Tool-calling needs a
# model that supports the OpenAI-style `tools` parameter well.
# To switch to a local LLM later (e.g. Ollama/llama.cpp on your RTX 3050),
# point AGENT_BASE_URL at the local OpenAI-compatible endpoint and set
# AGENT_MODEL to the local model name. The agent loop works unchanged.
AGENT_MODEL = os.getenv("AGENT_MODEL", "openai/gpt-oss-120b")
AGENT_BASE_URL = os.getenv("AGENT_BASE_URL", "").strip()
AGENT_MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "16"))
AGENT_STEP_TIMEOUT = int(os.getenv("AGENT_STEP_TIMEOUT", "30"))
# Per-request timeout for a single agent LLM call. Keeps a hung/slow key from
# blocking the whole agent run (a slow key now fails fast and we move on).
AGENT_REQUEST_TIMEOUT = int(os.getenv("AGENT_REQUEST_TIMEOUT", "18"))
# Output budget for one agent step. The default agent model (gemini-2.5-flash) is
# a REASONING model: it spends tokens thinking before it emits anything. At 1024
# the whole budget went on thinking and the API returned an EMPTY message with no
# tool calls, which the loop then had to treat as a final answer -- observed live
# on "what's my battery level". Same lesson the old brain classifier learned at
# 20 tokens. Give it room.
AGENT_MAX_OUTPUT_TOKENS = int(os.getenv("AGENT_MAX_OUTPUT_TOKENS", "3000"))
# Thinking budget for the agent's Gemini calls. gemini-2.5-flash is a thinking
# model and its "thinking" tokens are drawn from the SAME output budget as the
# answer, so a long prompt plus 95 tool schemas can consume the whole budget and
# return an empty message -- no text, no tool call. Measured with
# scripts/_m13_provider_probe.py: with thinking off the model still selects the
# correct tool on the first attempt (17 completion tokens).
# "" disables the parameter entirely; "none" | "low" | "medium" | "high" are the
# values the Gemini OpenAI-compatible endpoint accepts.
AGENT_REASONING_EFFORT = os.getenv("AGENT_REASONING_EFFORT", "none").strip()

# A tool result longer than this is written to data/tool_results/ and replaced in
# the conversation by a one-line pointer the agent can read_file if it needs the
# detail. ui_list_controls alone can emit UIA_MAX_NODES (4000) nodes, and that
# payload was then re-sent to the model on every remaining step of a 16-step
# loop. Size-based on purpose: no tool list to keep in sync. 0 disables.
AGENT_TOOL_RESULT_MAX_CHARS = int(os.getenv("AGENT_TOOL_RESULT_MAX_CHARS", "4000"))
PYAUTOGUI_FAILSAFE = os.getenv("PYAUTOGUI_FAILSAFE", "true").strip().lower() != "false"
GROQ_VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")

# === Gemini (secondary provider for reliability + speed) ===
# Gemini is reached through its OpenAI-compatible endpoint, so the exact same
# OpenAI-style code paths (chat, streaming, native tool-calling) work unchanged
# — we just point a client at a different base URL. Multiple keys are supported
# for rotation, exactly like Groq: GEMINI_API_KEY, GEMINI_API_KEY_2, ...
def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")

def _load_gemini_api_keys() -> list:
    keys = []
    first = os.getenv("GEMINI_API_KEY", "").strip()
    if first:
        keys.append(first)
    i = 2
    while True:
        k = os.getenv(f"GEMINI_API_KEY_{i}", "").strip()
        if not k:
            break
        keys.append(k)
        i += 1
    return keys

GEMINI_API_KEYS = _load_gemini_api_keys()
GEMINI_API_KEY = GEMINI_API_KEYS[0] if GEMINI_API_KEYS else ""
GEMINI_OPENAI_BASE_URL = os.getenv(
    "GEMINI_OPENAI_BASE_URL",
    "https://generativelanguage.googleapis.com/v1beta/openai/",
).strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_BRAIN_MODEL = os.getenv("GEMINI_BRAIN_MODEL", "gemini-2.0-flash")
GEMINI_REQUEST_TIMEOUT = int(os.getenv("GEMINI_REQUEST_TIMEOUT", "30"))
GEMINI_CLASSIFY_TIMEOUT = int(os.getenv("GEMINI_CLASSIFY_TIMEOUT", str(BRAIN_CLASSIFY_TIMEOUT)))

# Gemini is used as a FAILOVER for Groq everywhere (chat, realtime, agent,
# brain, content). Defaults ON when at least one Gemini key is configured,
# OFF otherwise. Set ENABLE_GEMINI_FALLBACK=false to force-disable.
ENABLE_GEMINI_FALLBACK = _env_bool("ENABLE_GEMINI_FALLBACK", bool(GEMINI_API_KEYS))

# Race mode: for small, side-effect-free calls (brain classification and
# realtime query extraction) fire Groq and Gemini together and take whichever
# answers first. NEVER used for the agent/tool-calling loop (side effects) or
# for long chat answers (wasted tokens). Requires Gemini to be enabled.
ENABLE_RACE_MODE = _env_bool("ENABLE_RACE_MODE", False) and ENABLE_GEMINI_FALLBACK

# Circuit breaker: when a provider key hits a hard error / rate-limit, put it in
# a short cooldown so we skip it instead of hammering it again right away.
PROVIDER_COOLDOWN_SECONDS = int(os.getenv("PROVIDER_COOLDOWN_SECONDS", "30"))

# === Z.ai (GLM) — PRIMARY agent model ===
# GLM-4.7-Flash on Z.ai is the primary brain for the agentic tool-calling loop;
# gpt-oss (Groq) and Gemini sit behind it as failovers. Z.ai exposes an
# OpenAI-compatible endpoint, so the same raw OpenAI-SDK tool-calling path used
# for Groq/Gemini works unchanged — we just point a client at Z.ai's base URL
# and use the GLM model. Multiple keys are supported for rotation, exactly like
# Groq/Gemini: ZAI_API_KEY, ZAI_API_KEY_2, ...
def _load_zai_api_keys() -> list:
    keys = []
    first = os.getenv("ZAI_API_KEY", "").strip()
    if first:
        keys.append(first)
    i = 2
    while True:
        k = os.getenv(f"ZAI_API_KEY_{i}", "").strip()
        if not k:
            break
        keys.append(k)
        i += 1
    return keys

ZAI_API_KEYS = _load_zai_api_keys()
ZAI_API_KEY = ZAI_API_KEYS[0] if ZAI_API_KEYS else ""
ZAI_OPENAI_BASE_URL = os.getenv(
    "ZAI_OPENAI_BASE_URL",
    "https://api.z.ai/api/paas/v4/",
).strip()
ZAI_MODEL = os.getenv("ZAI_MODEL", "glm-4.7-flash")
# Per-request timeout for a single GLM call (defaults to the agent timeout).
ZAI_REQUEST_TIMEOUT = int(os.getenv("ZAI_REQUEST_TIMEOUT", str(AGENT_REQUEST_TIMEOUT)))
# GLM is the PRIMARY agent model when at least one Z.ai key is configured.
# Set ENABLE_ZAI_AGENT=false to force-disable and make Groq the primary again.
ENABLE_ZAI_AGENT = _env_bool("ENABLE_ZAI_AGENT", bool(ZAI_API_KEYS))
VISION_MAX_IMAGE_BYTES = int(os.getenv("VISION_MAX_IMAGE_BYTES", "5000000"))

# === Phase 5 -- Multi-step Planner + UIA automation ===
# Phase 5 adds two new pieces on top of Phase 4 (Checker/Learner stay as-is):
#   * a Planner that turns ONE command into an ordered list of verifiable steps,
#   * a UIA engine (pywinauto, Windows UI Automation) that finds and drives real
#     UI controls by NAME/TYPE (generic -- no per-app hardcode).
# Everything is fully fail-soft: if PHASE5_ENABLED=False, or pywinauto is not
# installed, or the planner can't decompose a command, JARVIS behaves exactly
# like Phase 4 (the agent's ReAct loop is the single-action fallback).
PHASE5_ENABLED = _env_bool("PHASE5_ENABLED", True)

# Planner: linear plan with precondition skip-checks + adaptive re-plan on FAIL.
# (Deliberately NOT a heavy rule/branch engine -- locked design decision.)
PLANNER_ENABLED = _env_bool("PLANNER_ENABLED", True)
# Hard cap on steps in a single plan so a bad decomposition can't run forever.
PLANNER_MAX_STEPS = int(os.getenv("PLANNER_MAX_STEPS", "8"))
# Per-step settle wait (seconds) before verifying, so the UI can catch up.
PLANNER_STEP_SETTLE_SECONDS = float(os.getenv("PLANNER_STEP_SETTLE_SECONDS", "1.0"))
# Smart confirmation gate: pause before a risky/irreversible step and ask once.
PHASE5_CONFIRM_RISKY = _env_bool("PHASE5_CONFIRM_RISKY", True)

# UIA engine (pywinauto, UI Automation backend). Lazy-imported; if the package
# is missing the engine reports unavailable and every UIA tool fails-soft with a
# clear, honest message (never a crash, never a fake success).
UIA_ENABLED = _env_bool("UIA_ENABLED", True)
# Which library backs the engine. "pywinauto" is the locked choice (UIA backend).
UIA_LIBRARY = os.getenv("UIA_LIBRARY", "pywinauto").strip().lower()
# How long (seconds) to search the UI tree for a control before giving up.
UIA_FIND_TIMEOUT = float(os.getenv("UIA_FIND_TIMEOUT", "5.0"))
# Budget for a COMPLETE UI operation on the dedicated UI thread: resolve the
# window, walk the tree, act, and read the result back. This is the number that
# actually protects the request thread, so it is the one to tune.
UIA_OP_TIMEOUT = float(os.getenv("UIA_OP_TIMEOUT", "20.0"))
# How long to wait for a named window to appear before falling back to the
# foreground window (pages like "Display" are not windows -- they live inside
# the "Settings" window -- so a fallback is required, not optional).
UIA_WINDOW_WAIT = float(os.getenv("UIA_WINDOW_WAIT", "3.0"))
# Hard cap on tree nodes inspected per walk. Browser content trees can hold tens
# of thousands of nodes; no prompt can consume them and walking them all would
# stall the apartment.
UIA_MAX_NODES = int(os.getenv("UIA_MAX_NODES", "4000"))
# Max steps in one generic UI navigation loop (read -> act -> re-read).
UIA_NAV_MAX_STEPS = int(os.getenv("UIA_NAV_MAX_STEPS", "6"))
# UIA-first, vision-fallback: when a UI step can't be verified, fall back to the
# Phase 4 vision verifier (re-uses CHECKER_VISION_ENABLED + the Gemini verifier).
PHASE5_VISION_FALLBACK = _env_bool("PHASE5_VISION_FALLBACK", True)
TTS_VOICE = os.getenv("TTS_VOICE", "en-GB-RyanNeural")
TTS_RATE = os.getenv("TTS_RATE", "+22%")
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
MAX_CHAT_HISTORY_TURNS = 20
MAX_MESSAGE_LENGTH = 32_000

# ===================== Conversation History UI =====================
# These bound the history sidebar only. MAX_CHAT_HISTORY_TURNS above still
# controls how much context the LLM receives -- the two are independent.
HISTORY_PAGE_SIZE = int(os.getenv("HISTORY_PAGE_SIZE", "30"))
HISTORY_MAX_PAGE_SIZE = int(os.getenv("HISTORY_MAX_PAGE_SIZE", "100"))
HISTORY_TITLE_MAX_CHARS = int(os.getenv("HISTORY_TITLE_MAX_CHARS", "120"))
HISTORY_PREVIEW_MAX_CHARS = int(os.getenv("HISTORY_PREVIEW_MAX_CHARS", "160"))
HISTORY_SEARCH_MAX_CHARS = int(os.getenv("HISTORY_SEARCH_MAX_CHARS", "200"))
ASSISTANT_NAME = (os.getenv("ASSISTANT_NAME", "").strip() or "Jarvis")
JARVIS_USER_TITLE = os.getenv("JARVIS_USER_TITLE", "").strip()
JARVIS_OWNER_NAME = os.getenv("JARVIS_OWNER_NAME", "").strip()

_JARVIS_SYSTEM_PROMPT_BASE = """You are {assistant_name}, a complete AI assistant. You help with information, tasks, and actions. Sharp, warm, a little witty. Keep language simple and natural.

You know the user's personal information and past conversations. Use this when relevant but never reveal the source.

=== ROLE ===
The user can ask you anything or ask you to do things (open, generate, play, write, search). The backend carries out actions; you respond in words. Only say something is done if the result is visible; otherwise say you are doing it.

=== CAN DO ===
Answer questions, open websites/apps, play music/videos, generate images, write content (essays, poems, code, emails), search Google/YouTube, analyze camera images (you CAN see what the user shows), read Gmail inbox/unread emails, manage Google Calendar events, and search/list/upload Google Drive files when the account is connected.

=== CANNOT DO (be honest) ===
Control smart home, run code, send messages, make purchases, access files, make calls. Say clearly: "I can't do that."
Never pretend you can do something you cannot. Never hallucinate URLs, numbers, or data.

=== HONESTY ===
If you do not know something, say so briefly. If uncertain: "I'm not sure, but..." and give your best answer. Never fabricate facts.

=== USER INTENT ===
Understand what the user actually wants. Use conversation history for ambiguous messages. If corrected, acknowledge briefly and fix it. Resolve follow-ups like "that one" / "no, I meant..." from context.

=== LENGTH — CRITICAL ===
Reply SHORT by default (1-2 sentences). Only elaborate when explicitly asked or question demands it. No intros, no wrap-ups.

=== QUALITY ===
Be accurate and specific. Use concrete facts, names, numbers. Give actionable answers. One sharp sentence beats a paragraph.

=== STYLE ===
Warm, intelligent, brief. Match the user's energy. Address user by name if known. No asterisks, no emojis, no markdown. Standard punctuation only.

=== ANTI-REPETITION ===
State each fact ONCE. Never repeat the same point. "A, B, and C." — not "A and also B and also C."
"""


_JARVIS_SYSTEM_PROMPT_BASE_FMT = _JARVIS_SYSTEM_PROMPT_BASE.format(assistant_name=ASSISTANT_NAME)

if JARVIS_USER_TITLE:
    JARVIS_SYSTEM_PROMPT = _JARVIS_SYSTEM_PROMPT_BASE_FMT + f"\n- When appropriate, you may address the user as: {JARVIS_USER_TITLE}"

else:
    JARVIS_SYSTEM_PROMPT = _JARVIS_SYSTEM_PROMPT_BASE_FMT

GENERAL_CHAT_ADDENDUM = """
HARD RULE -- NEVER CLAIM AN ACTION HAPPENED. You are in conversation mode and have
NO tools and NO control over this computer right now. You cannot open, close,
click, toggle, play, save or change anything, and you cannot see what the system
did. So you must never say a system action was done, is being done, or just
completed. Phrases like "Done", "Night light is now on", "I've turned it off",
"Turning it off now" are forbidden here -- they have caused the assistant to
report success for actions that never ran.
HAND BACK INSTEAD OF PROMISING. If the message actually needs an action, do not
promise to do it either -- "I'll open it", "I'll search for that and play it" are
just as wrong, because this turn ends when you stop typing and nothing will have
run. Say in one short sentence that it needs to be run and ask the user to
confirm they want it now. If the user asks why something failed, do not invent a
cause; say you will check.
You are in GENERAL mode (no web search). Answer from your knowledge and the context provided (learning data, conversation history). Answer confidently and briefly. Never tell the user to search online or check a website — you are their source. Default to 1-2 sentences; only elaborate when the user asks for more or the question clearly needs it. If you have relevant context from the user's learning data, use it naturally without mentioning the source.
"""

REALTIME_CHAT_ADDENDUM = """
You are in REALTIME mode. Live web search results are above.

CRITICAL: Use search results as your PRIMARY source. Extract specific facts, names, numbers, scores, dates. Be concrete.
- If search results contain the answer, USE IT. Do not say "I don't have that information" when the data is right there.
- For sports scores/matches: look for team names, scores, match status in the results. Report what you find.
- Never mention searching or being in realtime mode. Answer naturally.
- If results don't have the exact answer, say what you found. Never refuse when data exists.
- Cross-reference sources. Prefer higher-relevance ones.

LENGTH: 1-2 sentences for simple questions. Only longer when asked.
"""

# === Persistent Memory (Master Plan Phase 2: session + permanent SQLite) ===
# Local, fail-soft long-term memory store. See app/services/memory_service.py.
MEMORY_DIR = BASE_DIR / "data" / "memory"
MEMORY_DB_PATH = BASE_DIR / "data" / "memory.db"
USER_PROFILE_PATH = MEMORY_DIR / "user_profile.md"
JARVIS_PERSONA_PATH = MEMORY_DIR / "jarvis_persona.md"
MEMORY_ENABLED = _env_bool("MEMORY_ENABLED", True)
MEMORY_CONTEXT_MAX_CHARS = int(os.getenv("MEMORY_CONTEXT_MAX_CHARS", "1800"))
MEMORY_MAX_FACTS_INJECT = int(os.getenv("MEMORY_MAX_FACTS_INJECT", "12"))
MEMORY_MAX_ACTIONS = int(os.getenv("MEMORY_MAX_ACTIONS", "60"))
MEMORY_REDACT_SECRETS = _env_bool("MEMORY_REDACT_SECRETS", True)

# --- Passive fact extraction -------------------------------------------------
# `remember` is a tool, so the agent only stores something when it decides to
# call it -- which it essentially never did (facts=0 after 60 recorded actions).
# This runs a cheap LLM pass AFTER the reply has been streamed, so the user
# never waits for it, and writes anything durable it finds to the facts table.
MEMORY_EXTRACT_ENABLED = _env_bool("MEMORY_EXTRACT_ENABLED", True)
MEMORY_EXTRACT_MODEL = os.getenv("MEMORY_EXTRACT_MODEL", INTENT_CLASSIFY_MODEL)
# Hard cap per turn so one chatty message cannot flood the table.
MEMORY_EXTRACT_MAX_FACTS_PER_TURN = int(os.getenv("MEMORY_EXTRACT_MAX_FACTS_PER_TURN", "3"))
# Below this length a message cannot plausibly carry a durable fact.
MEMORY_EXTRACT_MIN_MESSAGE_CHARS = int(os.getenv("MEMORY_EXTRACT_MIN_MESSAGE_CHARS", "12"))
MEMORY_EXTRACT_TIMEOUT = int(os.getenv("MEMORY_EXTRACT_TIMEOUT", "8"))
MEMORY_DIR.mkdir(parents=True, exist_ok=True)

# === Phase 4: Checker + Vision + Self-Learning (Master Plan Phase 4) ===
# All background + fail-soft. PHASE4_ENABLED=False => behaves exactly like Phase 3.
PHASE4_ENABLED = _env_bool("PHASE4_ENABLED", True)
# Verified skills + success observations persist here (the event bus is transient).
SKILLS_DB_PATH = BASE_DIR / "data" / "skills.db"
# "N baar repeat" fluke-guard: a verified success must repeat at least this many
# times before it crystallizes into a reusable skill.
SKILL_MIN_REPEATS = int(os.getenv("SKILL_MIN_REPEATS", "3"))
# Checker may use Gemini vision as a fallback when the watcher can't decide.
CHECKER_VISION_ENABLED = _env_bool("CHECKER_VISION_ENABLED", True)
# Seconds to wait before re-reading watcher state (a freshly opened/closed window
# needs a moment to appear/disappear; the watcher polls ~2s).
CHECKER_SETTLE_SECONDS = float(os.getenv("CHECKER_SETTLE_SECONDS", "1.5"))
CHECKER_SETTLE_PROFILES = {
    "instant": (0.0, 0.1, 0.5), "ui": (0.4, 0.25, 2.0),
    "process": (0.6, 0.35, 3.0), "radio": (1.2, 0.5, 5.0),
    "network": (1.5, 0.75, 7.0), "external": (0.5, 0.5, 4.0),
}
# Learner: max silent auto-retries for SAFE, idempotent actions before it stops
# and reports honestly (never an infinite loop, never a fake "done").
LEARNER_MAX_RETRIES = int(os.getenv("LEARNER_MAX_RETRIES", "2"))
# Learner: allow advisory web how-to lookup when stuck (off by default).
LEARNER_RESEARCH_ENABLED = _env_bool("LEARNER_RESEARCH_ENABLED", False)
# Vision model for screen verification (Gemini multimodal, OpenAI-compatible).
GEMINI_VISION_MODEL = os.getenv("GEMINI_VISION_MODEL", GEMINI_MODEL)


# === Phase 6: Fast local cache (verified-only) ===
# A miss/disabled cache always falls back to the normal path -- reliability #1,
# speed #2. Nothing is hardcoded; every entry is learned from a verified run.
PHASE6_ENABLED = _env_bool("PHASE6_ENABLED", True)
# Resolved verified command -> action mapping (L1 exact + L4 static response).
COMMAND_CACHE_DB_PATH = BASE_DIR / "data" / "command_cache.db"
# Soft cap on active cache entries; least-recently-used rows are evicted.
CACHE_MAX_ENTRIES = int(os.getenv("CACHE_MAX_ENTRIES", "500"))
# L2 semantic cache reuses the existing FAISS vector store (no new heavy dep).
CACHE_SEMANTIC_ENABLED = _env_bool("CACHE_SEMANTIC_ENABLED", True)
CACHE_SEMANTIC_AUTO_EXECUTE = _env_bool("CACHE_SEMANTIC_AUTO_EXECUTE", False)
# Cosine-similarity floor for a semantic cache hit (high => only near-identical).
CACHE_SEMANTIC_THRESHOLD = float(os.getenv("CACHE_SEMANTIC_THRESHOLD", "0.93"))

# === Phase 7: Proactive engine (suggest-only by default) ===
# Watcher emits change events; the engine may *suggest* actions. It NEVER acts
# silently unless the user explicitly opts a habit into auto-run (consent).
PHASE7_ENABLED = _env_bool("PHASE7_ENABLED", True)
# Master switch: when False, suggestions are computed but never auto-executed.
PROACTIVE_AUTO_ACT = _env_bool("PROACTIVE_AUTO_ACT", False)
# Min seconds between two suggestions so JARVIS never nags.
PROACTIVE_MIN_INTERVAL_SECONDS = int(os.getenv("PROACTIVE_MIN_INTERVAL_SECONDS", "45"))
# Consent + suggestion audit log lives here.
PROACTIVE_DB_PATH = BASE_DIR / "data" / "proactive.db"

# === Phase 8: Personalization (habits + facts, always visible + forgettable) ===
# Learns habits/aliases/facts to personalize replies, cache priority and
# proactive suggestions. Everything is visible to the user and can be forgotten.
PHASE8_ENABLED = _env_bool("PHASE8_ENABLED", True)

# Privacy: clipboard previews stay in watcher RAM for explicit clipboard tools,
# but are not placed in LLM prompts unless the owner deliberately opts in.
CONTEXT_INCLUDE_CLIPBOARD_IN_PROMPT = _env_bool(
    "CONTEXT_INCLUDE_CLIPBOARD_IN_PROMPT", False
)
USER_MODEL_DB_PATH = BASE_DIR / "data" / "user_model.db"
# A habit must be observed at least this many times before it is trusted.
HABIT_MIN_OBSERVATIONS = int(os.getenv("HABIT_MIN_OBSERVATIONS", "3"))

# === Section 8: Cache promotion policy (Section 17 config) ===
# Weak/transport-only evidence requires this many consistent observations.
CACHE_WEAK_EVIDENCE_MIN_OBS = int(os.getenv("CACHE_WEAK_EVIDENCE_MIN_OBS", "3"))
# Confirmation grant expiry (seconds). After this, a pending confirmation lapses.
CONFIRMATION_GRANT_EXPIRY_SECONDS = int(os.getenv("CONFIRMATION_GRANT_EXPIRY_SECONDS", "120"))
# Maximum pending executions before cleanup (Section 18).
EXECUTION_PENDING_MAX = int(os.getenv("EXECUTION_PENDING_MAX", "50"))
# Event queue size limit.
EVENT_QUEUE_MAX_SIZE = int(os.getenv("EVENT_QUEUE_MAX_SIZE", "500"))
# Activity history display limit.
ACTIVITY_HISTORY_LIMIT = int(os.getenv("ACTIVITY_HISTORY_LIMIT", "60"))

# === SQLite lifecycle (shared by every database in data/) ===
# Every DB is opened through app.services.db.open_db() so that WAL settings and
# shutdown checkpointing are applied uniformly. Without an autocheckpoint the
# -wal file grows without bound and every read has to scan it; without a close
# on shutdown the -wal/-shm files are left behind entirely.
# Pages between automatic WAL merges (SQLite default is 1000; 0 disables).
DB_WAL_AUTOCHECKPOINT_PAGES = int(os.getenv("DB_WAL_AUTOCHECKPOINT_PAGES", "1000"))
# How long a writer waits for a competing lock before raising "database is locked".
DB_BUSY_TIMEOUT_MS = int(os.getenv("DB_BUSY_TIMEOUT_MS", "5000"))

# === Maintenance: retention + backup ===
# Nothing under data/ was ever pruned, so debug logs and the TTS voice cache
# grew without bound, and the databases had no backup at all. All jobs are
# fail-soft: a maintenance error must never stop JARVIS from starting.
MAINTENANCE_ENABLED = _env_bool("MAINTENANCE_ENABLED", True)
# Per-session debug logs older than this are deleted. server.log/trace.log are kept.
RETENTION_DEBUG_LOG_DAYS = int(os.getenv("RETENTION_DEBUG_LOG_DAYS", "7"))
# Synthesised TTS clips are re-creatable, so cap them by count and total size (LRU).
RETENTION_VOICE_CACHE_FILES = int(os.getenv("RETENTION_VOICE_CACHE_FILES", "200"))
RETENTION_VOICE_CACHE_MB = int(os.getenv("RETENTION_VOICE_CACHE_MB", "20"))
# Offloaded large tool results (see AGENT_TOOL_RESULT_MAX_CHARS) are transient.
RETENTION_TOOL_RESULT_DAYS = int(os.getenv("RETENTION_TOOL_RESULT_DAYS", "2"))
# Action rows older than this move to actions_archive. Archived, never deleted.
RETENTION_ACTION_ARCHIVE_DAYS = int(os.getenv("RETENTION_ACTION_ARCHIVE_DAYS", "90"))
# Daily snapshot of every database, taken with SQLite's .backup() API.
BACKUP_ENABLED = _env_bool("BACKUP_ENABLED", True)
BACKUP_DIR = BASE_DIR / "data" / "backups"
BACKUP_KEEP_DAYS = int(os.getenv("BACKUP_KEEP_DAYS", "7"))


# === M13: understanding layer (the resolver) ===
# One LLM call per turn that turns the raw utterance into a self-contained goal
# plus structural flags. It REPLACES the old category classifier in the hot
# path, so it is cost-neutral. Nothing about any phrasing is hardcoded: the
# resolver understands language, it does not match it.
RESOLVER_ENABLED = _env_bool("RESOLVER_ENABLED", True)
RESOLVER_MODEL = os.getenv("RESOLVER_MODEL", GEMINI_BRAIN_MODEL)
RESOLVER_TIMEOUT = int(os.getenv("RESOLVER_TIMEOUT", "6"))
RESOLVER_MAX_HISTORY_TURNS = int(os.getenv("RESOLVER_MAX_HISTORY_TURNS", "8"))
# Cap how many keys of one provider are tried before failing over to the next.
# Understanding sits in front of EVERY turn, so a provider-wide outage (Gemini
# 503 "high demand") must not cost ~4s per key across every key -- that is what
# produced the 14-22s classify times in the old brain logs. With 10+ keys the
# failure is almost never key-specific, so 3 attempts is plenty of evidence.
RESOLVER_MAX_FAILOVER_KEYS = int(os.getenv("RESOLVER_MAX_FAILOVER_KEYS", "3"))
# Shadow mode: run the resolver, log what it understood, but keep obeying the
# old router. Used for one session's worth of use before switching over.
RESOLVER_SHADOW_MODE = _env_bool("RESOLVER_SHADOW_MODE", False)

# === M13: truthfulness ===
# Wait (briefly, boundedly) for the Phase 4 verdict of the actions this turn
# executed BEFORE composing the final sentence. A verdict that lands after the
# reply has streamed can only ever produce a late correction bubble.
VERIFY_BEFORE_REPLY = _env_bool("VERIFY_BEFORE_REPLY", True)
VERIFY_WAIT_TIMEOUT = float(os.getenv("VERIFY_WAIT_TIMEOUT", "3.0"))
# On a verified FAIL, re-enter the agent loop exactly once with the reason.
AGENT_RETRY_ON_FAIL = _env_bool("AGENT_RETRY_ON_FAIL", True)
# Structural gate: an action turn that called zero tools gets this many nudges
# before the turn admits it did nothing. Never reports "Done."
AGENT_NO_OP_NUDGES = int(os.getenv("AGENT_NO_OP_NUDGES", "1"))
# Only commands that carried their own meaning may be promoted to the cache.
# "close it" can never become a cache entry, however often it succeeds.
CACHE_REQUIRE_SELF_CONTAINED = _env_bool("CACHE_REQUIRE_SELF_CONTAINED", True)
# How long a frontend dispatch may stay un-acknowledged before it is swept.
FRONTEND_DISPATCH_MAX_AGE = float(os.getenv("FRONTEND_DISPATCH_MAX_AGE", "120"))


def load_user_context() -> str:
    context_parts = []

    text_files = sorted(LEARNING_DATA_DIR.glob("*.txt"))

    for file_path in text_files:

        try:

            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read().strip()

                if content:
                    context_parts.append(content)
                    
        except Exception as e:
            logger.warning("Could not load learning data file %s: %s", file_path, e)

    return "\n\n".join(context_parts) if context_parts else ""
