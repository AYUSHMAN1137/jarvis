# M13 — Understanding & Truthfulness

> **Status:** PLAN — approved for discussion, not yet implemented.
> **Author:** Claude Code (Opus 5), 2026-07-31
> **Evidence session:** `94bf07c2-10d7-4659-9728-e2e86d54469b`
> (`data/chats_data/chat_94bf07c210d746599728e2e86d54469b.json`,
> `data/debug_logs/session_2026-07-30_21-50-56_94bf07c2.log`)
> **Owner decisions locked:** see §2.

---

## 1. The problem, from evidence

Session `94bf07c2`, six turns. Every failure below is reproduced from the debug log,
not inferred.

| # | User | System behaviour | Mechanical cause |
|---|---|---|---|
| 1 | "Open YouTube." | `open_website(youtube)` → verdict **UNKNOWN** | ACK chain never registered (§3.1) |
| 2 | "Play ishq song." | LLM returned final text `"Done."`, **zero tool calls** | No structural gate on no-op action turns (§3.3) |
| 3 | "It's not playing." | Routed `general` → conversational chat | `_is_retry_complaint()` phrase list does not contain "not playing" |
| 4 | "It's a Pakistani song." | `general` → *"I'll look for…"* | `general` route has no tools; promise is unkeepable |
| 5 | "Bai Fahim Abdullah." | `general` → *"I'll search … and start playing it."* | Same |
| 6 | "Search for it." | `search_google(query="it")` | Regex fast path captured the literal pronoun |

By turn 6 the conversation contained every fact needed (*Ishq*, Pakistani, Fahim
Abdullah, play, YouTube). The system searched Google for the word **"it"**.

### 1.1 Root causes

1. **Language is interpreted by hardcoded lists**, in five independent places:
   `try_direct_web_command` (regex), `_is_retry_complaint`, `_is_affirmative` /
   `_is_negative`, `_needs_screen_look`, `_rule_based_primary`. Each miss requires a
   new string. This violates the project's own Rule #10.
2. **The agent may claim success without acting.** `agent_loop.py:505-509`.
3. **`general` and `task` are separate worlds.** The chat route has no tools, no state
   block, no context registry — but is free to promise actions.
4. **Frontend verification is dead** (§3.1). No web action can ever be verified, so
   nothing promotes to cache. This is why `command_cache` has ~6 entries.
5. **Verdicts arrive after the reply** and are never acted upon within the turn.

---

## 2. Locked decisions

| Decision | Choice | Rationale |
|---|---|---|
| Hardcoding line | Delete every list that guesses at **user language**. Keep factual OS/API data (`ms-settings:` URIs, OAuth scopes) — that is Windows' own vocabulary, not a guess. | Removing an address makes JARVIS worse without making it smarter. |
| Emergency keyword router | **Delete.** No LLM ⇒ honest "cannot reach reasoning engine". Verified cache entries still replay without an LLM. | It routed to an agent that also needs an LLM — it never helped. |
| Speed | **Earned, not pre-written.** Verified + self-contained commands replay from the existing Phase 6 cache. | Speed comes from real usage, not authored rules. |
| Unverifiable action | **Retry once, then admit.** | Owner's explicit choice. A lie costs more than 2s of honesty. |
| Sequencing | **Phase 1 bugs → Phase 2 redesign.** | Phase 2's cache learning depends on Phase 1's verification actually working. |

---

## 3. Phase 1 — Truthfulness (bug fixes)

Small, independently testable, no behavioural redesign. Ship and use before Phase 2.

### 3.1 Fix the dead frontend ACK chain

**Bug.** `action_sink.attach_dispatch()` (`action_sink.py:87`) mints a `dispatch_id`
and puts it in `_meta`. `Phase4Coordinator.register_dispatch()`
(`checker/coordinator.py:268`) records pending dispatches. **`register_dispatch` is
never called anywhere in the codebase.** Therefore:

- browser POSTs `/api/activity/frontend-ack` with the `dispatch_id`
- `acknowledge_dispatch()` (`coordinator.py:278`) pops `_pending_dispatches` → `None`
- `_dispatch_results[action_id]` is never written
- `_verify_frontend()` (`checker.py:632`) returns `UNKNOWN: browser never
  acknowledged the action` — **permanently, for every web tool, forever**

**Fix.**

1. `action_sink.attach_dispatch()` returns the `dispatch_id` it generated (or `None`
   when there are no actions).
2. `execution/coordinator.py:87` — capture it and register:

```python
dispatch_id = action_sink.attach_dispatch(context.execution_id, action.action_id)
if dispatch_id:
    self._phase4_or_default().register_dispatch(
        dispatch_id, action.action_id, tool=action.tool,
        execution_id=context.execution_id)
frontend_actions = action_sink.collect()
```

Registration happens inside `execute_action`, strictly before the chat layer yields
`_actions`, so the browser can never ACK before the dispatch is known.

3. Expire stale pending dispatches (browser closed / action never emitted) so
   `_pending_dispatches` cannot grow unbounded. `pending_dispatches(max_age)` already
   exists; add a sweep on read.

**Files:** `app/services/agent/action_sink.py`,
`app/services/agent/execution/coordinator.py`,
`app/services/agent/checker/coordinator.py`.

### 3.2 Fix duplicate + mis-attributed frontend actions

Two further bugs found while tracing 3.1. Both are in the sink's lifecycle.

**Bug A — actions replay within one run.** `collect()` (`action_sink.py:99`) returns
`dict(_bucket())` and **never clears**. `reset()` is called only at the start of
`run_stream` (`agent_loop.py:454`) and in the fast path. In a multi-step run, step 1
adds `wopens=[A]`; step 2 adds `googlesearches=[B]`; the step-2 emission at
`agent_loop.py:586` yields **both**, so the browser opens **A a second time**.

**Bug B — one ACK covers many actions.** `attach_dispatch` overwrites `b["_meta"]` on
every call, so only the last action's `action_id` survives. Earlier frontend actions
in the same run can never be acknowledged → permanent `UNKNOWN` even after 3.1.

**Fix.** Drain the sink per action instead of per run: `collect()` gains
`drain=True` semantics (return the bucket and reset it) at the point
`execute_action` reads it. Each frontend action then carries its own `_meta`, and the
agent loop emits each exactly once. `agent_loop.py:586` and `:594` emit whatever the
current action produced rather than the accumulated bucket.

**Test:** two web tools in one run ⇒ two `_actions` emissions, disjoint payloads, two
distinct `dispatch_id`s, both acknowledgeable.

### 3.3 Reject the silent no-op

**Bug.** `agent_loop.py:505-509`:

```python
if not tool_calls:
    final_text = (message.content or "").strip() or "Done."
    break
```

An action request that executed nothing is recorded as success. This is turn 2.

**Fix.** `run_stream` accepts a new argument `expect_action: bool` (Phase 1: passed
`True` whenever the route is `task`; Phase 2: driven by the resolver's `kind`).

On a no-tool-call final message where `expect_action` is true and
`executed_steps` is empty:

- **first occurrence** — do not break. Append a system turn stating that no tool was
  called and the request is therefore not done, and continue the loop (bounded: one
  nudge per run, counted separately from `AGENT_MAX_STEPS`).
- **second occurrence** — stop and return an honest failure string. Never `"Done."`.

The nudge is a control-flow gate in code, not a prompt instruction, so a model that
ignores prose cannot bypass it.

**Files:** `app/services/agent/agent_loop.py`, `app/services/chat_service.py`
(`_run_agent` passes `expect_action`).

### 3.4 Verify before speaking, retry once, then admit

**Today.** Verification is published on the event bus (`publish_action_done`,
`checker/coordinator.py:130`) and the verdict lands ~4s *after* the reply has already
streamed. `_failure_message()` exists precisely because JARVIS "ran out of turn"; the
correction arrives as a late bubble.

**Fix.** Give the turn the ability to wait, briefly and boundedly.

1. New `Phase4Coordinator.wait_for_verdict(action_id, timeout) -> Optional[dict]`.
   Implemented with a `threading.Event` keyed by `action_id`, resolved by the existing
   `"verified"` bus subscription (`coordinator.py:102`). Returns `None` on timeout.
2. In `_run_agent`, after the agent loop completes and before the final sentence is
   composed, wait up to `VERIFY_WAIT_TIMEOUT` (default 3.0s) for the verdicts of the
   actions this turn executed. Budget is per turn, not per action.
3. **On FAIL** — re-enter the agent loop once, with the failure reason appended to the
   goal (the existing `Learner` idempotency rules still apply: only
   `_IDEMPOTENT_SAFE` tools retry, never `_IRREVERSIBLE`).
4. **On FAIL again, or UNKNOWN** — the final sentence must state the limit of what was
   confirmed. Wording is generated, not templated: the agent is given the verdict and
   reason and composes one short clause.

Streaming stays responsive: progress activity events are emitted during the wait, so
the UI is not silent.

**Latency:** action turns grow by up to ~3s worst case; typical UI/toggle verdicts
settle in ~1.5s (`CHECKER_SETTLE_SECONDS`, `config.py:365`). Chat turns unaffected.

**Files:** `app/services/agent/checker/coordinator.py`,
`app/services/chat_service.py`.

### 3.5 Phase 1 tests

| File | Asserts |
|---|---|
| `tests/test_frontend_ack.py` | `attach_dispatch` → `register_dispatch` → `acknowledge_dispatch` → `dispatch_result(action_id)` is populated; `_verify_frontend` returns **PASS**, not UNKNOWN. Regression gate for the whole chain. |
| `tests/test_action_sink_isolation.py` | Two frontend tools in one run produce two disjoint payloads and two distinct `dispatch_id`s; no URL is emitted twice. |
| `tests/test_agent_no_op_guard.py` | Fake LLM returning text-only on an action turn ⇒ nudge issued; still text-only ⇒ honest failure, and the reply is **not** `"Done."`. |
| `tests/test_verify_before_reply.py` | Fake clock + fake verdicts: PASS ⇒ confident reply; FAIL ⇒ exactly one retry; UNKNOWN ⇒ reply states it could not confirm. Timeout path never hangs the turn. |

All use fake registries / fake bus / temp SQLite. No real browser, Wi-Fi, or power
actions (§16 of CLAUDE.md).

---

## 4. Phase 2 — Understanding

### 4.1 The resolver

One new service: `app/services/resolver.py`, singleton `get_resolver()`.

**Runs on every turn, before routing.** It **replaces** `BrainService.classify_primary`
in the hot path, so it does not add a call — it substitutes for one that already costs
~600ms.

**Input**
- the raw utterance
- last `RESOLVER_MAX_HISTORY_TURNS` (default 8) turns, user **and** assistant text
- the live state block (already built by `_build_state_block`, `agent_loop.py:361`)
- the last action of this session: tool, args, and **its verdict**
- relevant memory facts

**Output** — strict JSON, validated; any parse failure is fail-soft (§4.5):

```json
{
  "goal": "play the song Ishq by Fahim Abdullah on YouTube",
  "kind": "action|web_question|knowledge_question|visual|mixed",
  "self_contained": false,
  "refers_to_previous": true,
  "is_confirmation": null,
  "unresolved": [],
  "confidence": 0.0
}
```

Field notes:

- **`goal`** — a fully self-contained restatement with every reference resolved. This,
  not the raw utterance, is what reaches the agent loop. Turn 6 sends *"search for
  Ishq by Fahim Abdullah"*, never *"it"*.
- **`kind`** — replaces the five-category classifier.
- **`self_contained`** — whether the *original* utterance carried its own meaning.
  Sole input to cache eligibility (§4.4).
- **`refers_to_previous`** — replaces `_is_retry_complaint` with understanding.
- **`is_confirmation`** — `true`/`false`/`null`, replaces `_is_affirmative` /
  `_is_negative` for the dangerous-action gate (`chat_service.py:444-448`).
- **`unresolved`** — non-empty ⇒ **ask instead of guessing**. This is the safety valve
  that a regex never had.

**Model.** Same deterministic-failover discipline as the agent: Gemini primary, Groq
fallback, **never raced** (Rule #5). Config `RESOLVER_MODEL`, default
`GEMINI_BRAIN_MODEL`.

### 4.2 Routing changes (`chat_service.py:469-571`)

```
utterance
  → resolver  → goal + kind + flags
  → if unresolved     → ask a clarifying question, end turn
  → if is_confirmation is not None and a dangerous action is pending → grant/cancel
  → if kind == action|mixed|visual → agent loop, driven by GOAL (not the utterance)
  → if kind == web_question        → realtime
  → if kind == knowledge_question  → chat
```

**Two independent guards keep turn 3 from recurring:**

1. the resolver understands the reference, and
2. a **state-based** guard: if the session's last action verdict is `FAIL`/`UNKNOWN`
   **and** `refers_to_previous` is true, the conversational route is not permitted —
   the turn goes to the agent. This is a state check, not a language check, so it
   holds even if the resolver misjudges.

The `general`/`task` split survives only as an *outcome*, never as a wall: the
conversational route is given an explicit constraint that it cannot act and must hand
back if action is required.

### 4.3 Deletions

| Symbol | File | Replaced by |
|---|---|---|
| `try_direct_web_command` (+ `_OPEN_VERBS`, `_PLAY_VERBS`, `_SEARCH_VERBS`) | `tools/web_tools.py:56-109` | resolver + learned cache |
| fast-path block | `chat_service.py:643-698` | resolver + learned cache |
| `_is_retry_complaint` | `chat_service.py:1026-1046` | `refers_to_previous` |
| `_is_affirmative` / `_is_negative` | `chat_service.py:971-995` | `is_confirmation` |
| `_needs_screen_look` | `chat_service.py:997-1023` | `kind` |
| `_rule_based_primary` (+ `action_signals`) | `brain_service.py:367-403` | honest offline error |
| `_SITE_MAP` | `tools/web_tools.py:28-38` | `_normalize_url` + resolver |
| `_APP_ALIASES`, `_CLOSE_PRONOUNS` | `tools/desktop_tools.py:47, 74` | resolver + existing Start-Menu discovery |

`BrainService` is retired once the resolver ships; `brain_service.py` is deleted, not
left dead. **Kept:** `_SETTINGS_URIS` (`system_info_tools.py:314`) — Windows'
own addresses, not language.

~150 lines of guessing removed.

### 4.4 Cache eligibility

`CacheCoordinator` promotion gains one condition: promote only when
`self_contained` was true **and** every action verified `PASS`. `"close it"` can never
be promoted, regardless of how often it succeeds. Recorded alongside the existing
`cacheable=False` metadata check in `_is_uncacheable()`.

This is what makes speed *earned*: a phrasing you use repeatedly, that has been proven
to work, replays instantly. Nothing is fast because someone wrote a rule for it.

### 4.5 Fail-soft behaviour

Per Rule #6, every new element degrades rather than breaks:

| Failure | Behaviour |
|---|---|
| Resolver LLM unavailable | Try cache (needs no LLM). Miss ⇒ honest "cannot reach my reasoning engine." **No keyword guessing.** |
| Resolver returns unparseable JSON | One re-ask; then treat the raw utterance as the goal with `kind=action` if a tool route was already chosen, else chat. |
| `wait_for_verdict` times out | Treated as UNKNOWN → admit, do not retry. |
| Phase 4 disabled | §3.4 is a no-op; Phase 1's other fixes still apply. |

### 4.6 Transparency

The resolver's `goal` is emitted as an activity event and shown in the Activity panel
("Understood: play *Ishq* by Fahim Abdullah on YouTube"). The owner can see what was
understood **every turn**, which is the mitigation for a confidently-wrong resolution.

### 4.7 Phase 2 tests

| File | Asserts |
|---|---|
| `tests/test_resolver.py` | Fake LLM: pronoun resolution, ellipsis, Hindi/English mixing, `unresolved` triggers a question, malformed JSON falls back safely. |
| `tests/test_resolver_routing.py` | Replays session `94bf07c2` turn by turn: turn 3 reaches the agent, turn 6 never receives `"it"`. |
| `tests/test_no_hardcoded_language.py` | **Regression gate for Rule #10.** Asserts the deleted symbols no longer exist. Fails CI if a phrase list is reintroduced. |
| `tests/test_cache_self_contained.py` | Context-dependent commands never promote, even after repeated PASS. |

Existing suites that must stay green: `test_verifier_coverage.py`,
`test_execution_safety.py`, `test_command_cache_execution.py`,
`test_user_model_idempotency.py`, `test_conversation_history*.py`.

---

## 5. Configuration (`config.py`)

```python
# --- M13: understanding layer ---
RESOLVER_ENABLED           = _env_bool("RESOLVER_ENABLED", True)
RESOLVER_MODEL             = os.getenv("RESOLVER_MODEL", GEMINI_BRAIN_MODEL)
RESOLVER_TIMEOUT           = int(os.getenv("RESOLVER_TIMEOUT", "6"))
RESOLVER_MAX_HISTORY_TURNS = int(os.getenv("RESOLVER_MAX_HISTORY_TURNS", "8"))

# --- M13: truthfulness ---
VERIFY_BEFORE_REPLY        = _env_bool("VERIFY_BEFORE_REPLY", True)
VERIFY_WAIT_TIMEOUT        = float(os.getenv("VERIFY_WAIT_TIMEOUT", "3.0"))
AGENT_RETRY_ON_FAIL        = _env_bool("AGENT_RETRY_ON_FAIL", True)
AGENT_NO_OP_NUDGES         = int(os.getenv("AGENT_NO_OP_NUDGES", "1"))
CACHE_REQUIRE_SELF_CONTAINED = _env_bool("CACHE_REQUIRE_SELF_CONTAINED", True)
```

Every flag defaults **on** and can be switched off to restore prior behaviour without
a code change.

---

## 6. Order of work

**Phase 1** (ship and use before starting Phase 2)

1. 3.1 ACK chain + tests
2. 3.2 sink isolation + tests
3. 3.3 no-op guard + tests
4. 3.4 verify-before-reply + tests
5. `python scripts/verdict_report.py --write-baseline` — expect UNKNOWN share to drop
   materially from the ~38% baseline once web actions can verify

**Phase 2**

6. `resolver.py` + tests, behind `RESOLVER_ENABLED`, running alongside the old router
   in shadow mode (logged, not obeyed) for one session's worth of use
7. Switch routing to the resolver; delete §4.3 symbols; delete `brain_service.py`
8. Cache eligibility from `self_contained`
9. Activity-panel transparency
10. `CLAUDE.md` updated in the same pass — §5 endpoints, §8 phases, §12 config, §14
    rules, §16 tests, §19 decision log

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| Resolver misunderstands and the agent acts confidently on the wrong goal | `unresolved` ⇒ ask, never guess. Resolved goal shown in the UI every turn. `RESOLVER_ENABLED=False` reverts instantly. |
| Action turns ~1–3s slower | Bounded by `VERIFY_WAIT_TIMEOUT`. Chat unaffected. Repeat commands get *faster* than today once cache learning works. |
| All LLM keys down ⇒ JARVIS cannot route | Accepted, per §2. Verified cache entries still replay offline. Honest error otherwise. |
| One extra LLM call per action turn on retry | Explicitly accepted by the owner ("use multiple API calls"). |
| Large diff across `chat_service` / `agent_loop` / `checker` | Phase split; every change flag-gated and independently tested. |

---

## 8. Out of scope

M9 (SKILL.md), M10 (MCP), voiced reminders, Telegram/WhatsApp, Spotify, Hindi TTS
voice, history follow-ups. Unchanged: no `run_shell_command` tool; `_guard_path`
remains an accident guard, not a security boundary.

---

## 9. Definition of done

- A replay of session `94bf07c2` produces: turn 2 acts or admits; turn 3 reaches the
  agent; turn 6 never receives `"it"`.
- `_verify_frontend` can return `PASS`. Web commands appear in `command_cache`.
- No reply says an action happened unless a verdict confirms it.
- `tests/test_no_hardcoded_language.py` green — no phrase list can return.
- `python -m compileall -q app tests scripts run.py config.py` and
  `node --check web/script.js` clean; full suite green (215 + new).
