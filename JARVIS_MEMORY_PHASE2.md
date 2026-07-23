# JARVIS — Phase 2: Memory (Long-Term + Self-Learning Foundation)

> Status: **Phase 2 / M1 implemented.** Lean, fail-soft, no-LLM, future-aligned.
> This file documents exactly what was built so nothing is lost.

---

## 1. Goal (from Master Smartness Plan — Phase 2)

Give JARVIS a real memory so it stops being "goldfish":

- **Session memory** (short-term) — already existed (last 20 turns). Kept as-is.
- **Permanent memory** (long-term) — NEW. Stored locally, injected into every reply, learns over time.
- Honour the whole-phase cautions:
  - **Reliability #1** — memory must NEVER crash chat (fail-soft everywhere).
  - **Speed #2** — no network, no LLM on the hot path; SQLite + regex only.
  - **Privacy** — 100% local; secrets are never saved.
  - **No heavy deterministic layer yet** — only a few light auto-capture rules.
  - **Correction memory** — remember mistakes so they aren't repeated.

---

## 2. Memory types implemented (M1)

| Type | Where | How it works |
|------|-------|--------------|
| **Working memory** | existing chat history (last 20 turns) | unchanged |
| **Profile memory** | `database/memory/user_profile.md` + `jarvis_persona.md` | always injected into the system prompt |
| **Fact memory** | SQLite `facts` table | durable facts (name, preferences, projects) |
| **Action memory** | SQLite `actions` table | every tool the agent runs is logged (feeds "isko/ye/wo" context resolution later) |
| **Correction memory** | SQLite `corrections` table | "don't do X, do Y" — injected so mistakes aren't repeated |
| Routine memory | *(deferred to M3)* | — |

---

## 3. Files

### New files
- **`app/services/memory_service.py`** — the whole memory engine.
  - `MemoryService` class (all methods fail-soft / never raise into chat).
  - SQLite store with **WAL** mode + **FTS5** full-text index (auto-fallback to `LIKE` if FTS5 missing).
  - Seeds the two profile markdown files on first run.
  - Module helpers: `get_memory()` (singleton, returns a safe `_NullMemory` if init fails) and `augment_system_prompt()`.
- **`app/services/agent/tools/memory_tools.py`** — 4 agent tools:
  - `remember`, `recall`, `forget`, `note_correction`.

### Runtime-created (not committed)
- `database/memory.db` (+ `-wal`, `-shm`)
- `database/memory/user_profile.md`
- `database/memory/jarvis_persona.md`

### Edited files (7 wiring points)
| File | What changed |
|------|--------------|
| `config.py` | Added memory config constants (paths, `MEMORY_ENABLED`, char/inject caps, `MEMORY_REDACT_SECRETS`). |
| `app/services/groq_service.py` | After building the system prompt, calls `augment_system_prompt()` — covers **both** normal chat and realtime (RealtimeGroqService inherits this). |
| `app/services/chat_service.py` | (a) Logs each executed tool to action memory. (b) Runs `auto_capture()` on every user message. |
| `app/services/agent/agent_loop.py` | Logs each agent tool execution to action memory. |
| `app/main.py` | Warms up memory on startup (creates DB + seeds profile files). |
| `app/services/agent/tools/__init__.py` | Registers the new memory tools. |

---

## 4. How memory is RECALLED (read path)

Every reply gets a memory block injected into the system prompt via `get_prompt_context()`:

```
=== MEMORY (long-term, about the user) ===
[Assistant notes]      <- jarvis_persona.md
[About the user]       <- user_profile.md
[Remembered facts]     <- top facts from SQLite
[Recent corrections]   <- so mistakes aren't repeated
[Last action]          <- most recent tool run
[Possibly relevant]    <- FTS5 search against the current question
```

- Always-on profile + facts → personality/continuity.
- Question-specific FTS5 search → relevant memories surface on demand.
- Hard cap (`MEMORY_CONTEXT_MAX_CHARS`, default 1800) keeps the prompt small = fast.

On-demand the model can also call the **`recall`** tool for a deeper lookup.

---

## 5. How memory is LEARNED (write path)

1. **Explicit** — user says "remember that ..." → `auto_capture()` saves it, or the model calls the `remember` tool.
2. **Heuristic auto-capture** — light regex only (name, preferences like "I prefer / default ... is", "remember that ..."). No LLM, no guessing.
3. **Action logging** — every tool execution is recorded automatically.
4. **Corrections** — user corrections saved via `note_correction` / `record_correction`.

### Safety guards
- **Secret scan** — anything that looks like a password / API key / long token / card number is **refused, never stored**.
- **Size caps** — values truncated; action log trimmed to `MEMORY_MAX_ACTIONS`.
- **Dedup** — same fact key updates in place instead of duplicating.
- **Fail-soft** — any DB error is swallowed; chat continues normally.

---

## 6. Verification done

- `py_compile` passes on all new + edited files.
- Functional smoke test in sandbox: remember / recall / auto-capture / action logging / correction / profile injection all work; FTS5 active.
- Secret refusal verified (password string rejected).
- Name auto-capture verified clean: "my name is Ayush and remember that I work night shifts" → name = **Ayush** (not the whole sentence); "mera naam Ayush Sharma hai" → **Ayush Sharma**.

> Note: full end-to-end (with Groq/voice) must be tested on the Windows machine after `pip install -r requirements.txt` + restart, since the sandbox has no network.

---

## 7. Deliberately deferred

- **M2** — LLM "dreaming"/consolidation, memory viewer + edit UI, pause toggle.
- **M3** — routine/skill memory, watcher-linked episodic timeline, context compaction, optional vector reranking.
- **Vector store** — existing FAISS code is **parked** (not on the memory hot path), not deleted.

---

## 8. Config knobs (in `config.py`)

| Key | Default | Meaning |
|-----|---------|---------|
| `MEMORY_ENABLED` | `True` | master on/off (env `MEMORY_ENABLED`) |
| `MEMORY_CONTEXT_MAX_CHARS` | `1800` | max chars injected per reply |
| `MEMORY_MAX_FACTS_INJECT` | `12` | max always-on facts |
| `MEMORY_MAX_ACTIONS` | `60` | action-log retention |
| `MEMORY_REDACT_SECRETS` | `True` | refuse to store secrets |
