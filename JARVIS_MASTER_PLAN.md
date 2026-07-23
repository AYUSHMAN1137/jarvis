# JARVIS — Master Smartness Plan (Watcher + Memory + Context + Proactive + Multi-step + Personalization)

> Status: PLAN / DISCUSS stage. Not built yet. Added 2026-06-17 (Ayush).
> Priorities (user): reliability #1, decent speed #2, voice daily-driver stays.
> NOTE: heavy deterministic layer abhi NAHI banana — keep simple/LLM-driven.

## The one unifying insight
Saari smartness ek hi cheez pe depend karti hai: SHARED CONTEXT.
- Watcher  = LIVE state (system me abhi kya ho raha hai)
- Memory   = HISTORY state (pehle kya hua, user prefs)
- Baaki sab (context/proactive/multistep/personalization) inhi 2 ko consume karte hai.

## Layered architecture
Layer 0 — WATCHER (System State Service): background daemon, system start pe on. Tracks: open apps(name->PID->window), active window, recent files, clipboard, toggles(wifi/bt/vol). Event-driven + light polling. Local API/SQLite se state expose.
Layer 1 — MEMORY: session(short) + persistent(long, SQLite/JSON). User facts, prefs, frequent commands, corrections. "yaad rakhe".
Layer 2 — CONTEXT RESOLVER ("isko/ye/wo"): watcher(last opened/active) + memory(last mentioned) se ambiguous reference resolve.
Layer 3 — MULTI-STEP PLANNER: goal ko steps me toda, har step ke baad watcher se verify.
Layer 4 — PROACTIVE ENGINE: watcher events pe rules -> suggest/act. Permission-gated.
Layer 5 — PERSONALIZATION: memory history se aadat seekhe -> context+proactive ko behtar kare.

## Dependency order (MUST follow)
Watcher -> Memory -> Context -> Multi-step -> Proactive -> Personalization
(upar wala neeche wale ka data use karta hai; isliye neeche se banao.)

## Phased build
- Phase 1: Watcher core (app/window registry) + open/close wire. (Foundation + current bug fix.)
- Phase 2: Memory store (session + persistent SQLite); conversation+actions hook.
- Phase 3: Context resolver (isko/ye/wo) using watcher+memory.
- Phase 4: Multi-step planner with per-step verification.
- Phase 5: Proactive engine (triggers on watcher events) + permission gating.
- Phase 6: Personalization (habit learning) feeding everything.

## Code mapping (current JARVIS)
- Watcher: NEW app/services/watcher/state_service.py ; desktop_tools open/close read/write registry.
- Memory: NEW app/services/memory/ (SQLite); brain_service/chat_service prompt me inject.
- Context resolver: small pre-tool step in brain/agent_loop.
- Multi-step: enhance agent_loop (plan + verify).
- Proactive: watcher emits events -> rules engine -> notifications/actions.
- Personalization: analytics over memory.

## Design cautions
1. Reliability #1: watcher fail-safe. Agar watcher down -> JARVIS current behaviour pe fallback, kabhi crash nahi.
2. Resource: single light daemon, event-driven, debounce.
3. Privacy: sab LOCAL, secrets never logged, sensitive encrypt.
4. Speed: state read fast (in-memory/SQLite), voice latency na badhe.
