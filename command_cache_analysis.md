# Command Cache (Phase 6) — Deep Analysis

## TL;DR: Kya ho raha hai?

**Cache ka code sahi likha hai, lekin practically kaam nahi kar raha.** Database me sirf 1 entry hai, uspe 0 hits hain, aur skills DB me 0 entries hain. Matlab cache kabhi bhi properly fill nahi hua aur kabhi bhi kisi command ko fast-path se execute nahi kiya.

---

## 1. Cache Kaise Kaam Karna Chahiye (Design)

```
User: "open notepad"
  │
  ├─ Step 1: ChatService checks Phase 6 cache (lookup)
  │   └─ MISS → normal path (LLM picks tool → execute)
  │
  ├─ Step 2: Agent Loop executes tool
  │   └─ publish_action_done("open_application", {name: "notepad"}, result)
  │
  ├─ Step 3: Event Bus fires "action.done"
  │   └─ Checker subscribes → verifies (watcher state / vision)
  │
  ├─ Step 4: Checker publishes "verified" event
  │   └─ verdict: PASS or FAIL
  │
  ├─ Step 5: Phase 6 Coordinator subscribes to "verified"
  │   ├─ PASS → cache.put("open notepad", tool, {tool, args})
  │   └─ FAIL → cache.evict("open notepad")
  │
  └─ Next time user says "open notepad":
      └─ Cache HIT → skip LLM → execute directly → FAST!
```

---

## 2. Actual Database State (Right Now)

### command_cache.db — Only 1 entry:
```
trigger: "open notepad and write a paragraph for college leave"
kind: "tool"
hits: 0          ← NEVER USED
fail_count: 0
status: "active"
created_at: 2026-07-24T11:32:09
last_used: None  ← NEVER HIT
```

### skills.db — 0 entries (empty)

**Matlab**: Cache me 1 command store hui, lekin wo itni specific hai ("open notepad and write a paragraph for college leave") ki user ne dobara exactly wahi command nahi di. Aur hits = 0 matlab cache lookup ne kabhi bhi kisi command ko match nahi kiya.

---

## 3. Problems Found (Why Cache Isn't Really Working)

### Problem 1: Exact Match Only — Bahut Strict

```python
# command_cache.py line 95-100
@staticmethod
def normalize(text: Any) -> str:
    s = str(text or "").strip().lower()
    s = " ".join(s.split())
    return s.strip(" .!?,;:\"'")
```

Cache sirf **exact normalized match** pe hit karta hai. Matlab:
- "open notepad" ✅ cached
- "open notepad please" ❌ MISS (different text)
- "notepad kholo" ❌ MISS (Hindi)
- "launch notepad" ❌ MISS (different verb)

**Real world me user kabhi bhi exactly same sentence dobara nahi bolta.** Isliye cache almost never hit karega.

### Problem 2: Referential Check Bahut Aggressive

```python
# coordinator.py line 109-121
def _is_referential(self, text: Any) -> bool:
    try:
        from app.services.context.context_engine import detect_reference
        return bool(detect_reference(str(text or "")))
    except Exception:
        return True  # ← ON ANY ERROR, BLOCKS CACHING
```

`detect_reference` checks for pronouns like "it", "this", "that", "ye", "wo", "isko", etc.

**Problem**: The word **"is"** is in the pronoun list! So any Hindi sentence with "is" (which is extremely common) gets blocked:
- "notepad **is** kholo" → blocked (contains "is")
- "volume badhao **is** time" → blocked

Also, if the import fails for ANY reason, it returns `True` (referential) → **nothing ever gets cached**.

### Problem 3: Dangerous Tool Check — Fails Safe = Blocks Everything

```python
# coordinator.py line 93-107
def _is_dangerous(self, tool: Any) -> bool:
    if not tool:
        return True  # ← No tool name = dangerous
    try:
        from app.services.agent.tool_registry import registry
        return bool(registry.is_dangerous(tool))
    except Exception:
        return True  # ← ANY import error = dangerous = NO CACHE
```

If the registry import fails (circular import, module not loaded yet), **every tool is treated as dangerous** → nothing gets cached.

### Problem 4: Verification Chain is Long and Fragile

For a command to get cached, ALL of these must succeed:
1. ✅ Agent loop executes tool
2. ✅ `publish_action_done()` fires (fail-soft, can silently fail)
3. ✅ Event bus dispatches to Checker (thread pool, can drop)
4. ✅ Checker waits 1.5s settle time + polls up to 4s
5. ✅ Checker gets watcher state (watcher must be running)
6. ✅ Checker verdict = PASS (not UNKNOWN, not FAIL)
7. ✅ Phase 6 coordinator receives "verified" event
8. ✅ Not referential, not dangerous
9. ✅ `cache.put()` succeeds

**Any single failure in this 9-step chain = command never gets cached.**

### Problem 5: Checker Often Returns UNKNOWN

The Checker uses watcher state to verify. But:
- If watcher hasn't refreshed yet (2s cycle) → stale state → UNKNOWN
- If the tool isn't in a "verifiable family" (open/close/toggle) → UNKNOWN
- UNKNOWN ≠ PASS → **not cached**

Only `open_*`, `close_*`, and toggle tools (wifi, bluetooth, volume, brightness) can be verified by the watcher. Everything else (search, file ops, email) → UNKNOWN → never cached.

### Problem 6: The One Cached Entry is Too Specific

The only cached entry is: `"open notepad and write a paragraph for college leave"`

This is a **multi-intent command** that was stored as `kind="tool"` with a single tool. The user will NEVER say this exact sentence again. A useful cache entry would be:
- "open notepad" → `open_application({name: "notepad"})`
- "close notepad" → `close_application({name: "notepad"})`
- "play music" → `play_music({query: ""})`

---

## 4. What IS Working (The Good Parts)

1. **SQLite storage** — DB exists, WAL mode, schema is correct ✅
2. **LRU eviction** — cap at 500 entries, evicts least-used ✅
3. **Self-healing** — if a cached command fails on replay, it gets evicted ✅
4. **Safety gates** — dangerous tools never cached, referential commands never cached ✅
5. **Fail-soft** — cache failure never crashes JARVIS ✅
6. **The lookup path in chat_service.py** — correctly wired at line 439-498 ✅
7. **Replay re-verification** — cached commands still go through Phase 4 on replay ✅

---

## 5. Flow Diagram (Actual vs Expected)

### Expected (How it SHOULD work):
```
"open notepad" → MISS → LLM → tool → verify PASS → CACHE IT
"open notepad" → HIT! → execute directly (0.5s instead of 3-5s)
```

### Actual (What's happening):
```
"open notepad" → MISS → LLM → tool → verify → UNKNOWN/FAIL → NOT CACHED
"open notepad" → MISS → LLM → tool → verify → UNKNOWN/FAIL → NOT CACHED
(every time = full LLM path, no speedup ever)
```

---

## 6. Root Cause Summary

| # | Problem | Impact |
|---|---------|--------|
| 1 | Exact-match only (no fuzzy/semantic) | 99% of repeats miss |
| 2 | "is" in pronoun list blocks Hindi commands | Many commands blocked |
| 3 | Fail-safe = True on import errors | Everything blocked if any module issue |
| 4 | 9-step verification chain | Any break = no cache |
| 5 | Checker returns UNKNOWN for most tools | Only open/close/toggle cacheable |
| 6 | Cached entry too specific | Never matches again |

---

## 7. Verdict

**Cache ka architecture sahi hai, code quality acchi hai, lekin practically ye kaam nahi kar raha kyunki:**

1. Verification (Phase 4 Checker) mostly UNKNOWN return karta hai → PASS nahi milta → cache fill nahi hota
2. Exact match bahut strict hai → hit rate ~0%
3. Referential check bahut aggressive hai → bahut saare commands block ho jaate hain

**Hit rate right now: 0%** (1 entry, 0 hits, 0 last_used)

---

## 8. Fixes Needed (Priority Order)

1. **Checker should default to PASS for simple tools** — If tool returned non-ERROR result and it's a simple open/close, treat as PASS (not UNKNOWN)
2. **Add fuzzy/semantic matching** — "open notepad" should match "notepad kholo", "launch notepad"
3. **Remove "is" from pronoun list** — It's too common in Hindi
4. **Cache at the tool level, not the full sentence** — Store "open_application(notepad)" as the key, not the full user message
5. **Add a "trust after N successes" path** — If same tool+args worked 3 times, cache it even without formal PASS


---

## 9. Implementation Update — Execution-Level Cache Foundation

The first correctness upgrade has now been implemented without adding command-specific conditions.

### What changed

1. Every agent run now receives a unique `execution_id`.
2. Every executed tool call receives an `action_id`.
3. Individual checker verdicts preserve both IDs.
4. The agent publishes one complete execution manifest after the run finishes.
5. Phase 6 joins asynchronous verdicts to that manifest.
6. A command is promoted only when the execution completed successfully and every expected action received `PASS`.
7. Multi-action executions are stored as complete `plan` entries instead of allowing individual tool events to overwrite the same command.
8. Cached plans now have a safe sequential replay path.
9. Every replayed step is checked again, and a failed replay invalidates the cache entry.
10. Old atomic callers without execution metadata remain backward compatible.

### Why this matters

The earlier design could store a full multi-intent sentence against only one asynchronously verified tool. The new design promotes the complete resolved execution as one unit. It is generic: it does not contain rules for Notepad, Chrome, volume, or any specific command.

### Validation

Regression tests cover:

- Complete multi-step promotion
- Out-of-order asynchronous verdicts
- Rejection of `UNKNOWN` verdicts
- Eviction after `FAIL`
- Rejection of incomplete executions

The next architectural layer should add schema-driven tool policies and guarded canonical/semantic matching. Exact matching remains the safe Level 1 fallback until those layers are implemented and tested.
