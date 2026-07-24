# JARVIS — Final Integration, Reliability, Learning, and Observability Plan

​
**Audience:** AI coding agent working inside the JARVIS repository  
**Priority order:** Reliability → Safety → Correctness → Observability → Speed → Learning  
**Implementation style:** Generic, metadata-driven, fail-soft, testable, and extensible  
**Important:** Do not hardcode command phrases, application names, websites, tool combinations, or language-specific command lists to make individual tests pass.
​

---

​

## 1. Purpose and Product Direction

​
JARVIS is not meant to become a collection of special-case command handlers. It should become a general execution system that can:
​

1. Understand a user request.
2. Resolve it into one action or an ordered action graph.
3. Ask for confirmation when the exact action requires it.
4. Execute each action once.
5. Distinguish between “the tool returned” and “the requested effect happened.”
6. Verify effects with the best available evidence.
7. Learn only from trustworthy, complete executions.
8. Replay safe verified executions without calling an LLM unnecessarily.
9. Use context and user history without leaking sensitive data or creating accidental habits.
10. Show the complete meaningful lifecycle in debug logs, the Activity panel, and the Control Center.
    ​
    The architecture must support future tools without adding another `if command == ...` branch. New tools should integrate through tool metadata, schemas, verification capabilities, risk metadata, and shared execution contracts.
    ​

---

​

## 2. Review of the Supplied Plan

​
The supplied plan has the correct high-level goal: connect execution, checking, learning, caching, planning, context, personalization, and proactive behavior. However, several statements and proposed code changes do not match the current repository and would create regressions.
​

### 2.1 Correct ideas in the supplied plan

​

- Every action needs an outcome lifecycle.
- Cache promotion must depend on verification.
- Multi-step execution needs ordered steps, honest stopping, and per-step results.
- Context should influence planning and reference resolution.
- Proactive behavior must remain conservative.
- Integration tests are required.
- Every phase must fail softly and preserve the normal agent fallback.
  ​

### 2.2 Incorrect or outdated claims

​
The coding agent must not implement the original snippets without first checking the current source.
​

1. **“Phase 4 is dead code” is no longer true.**  
    `AgentLoop` publishes action results through `Phase4Coordinator.publish_action_done()`. Phase 4 subscribes, verifies asynchronously, and publishes `verified` events.
   ​
2. **“Phase 6 never learns” is no longer true.**  
    Phase 6 subscribes to `verified` and `execution.completed`. It joins verdicts using `execution_id` and `action_id`, waits for a complete execution manifest, promotes only complete all-PASS executions, supports tool and plan entries, and evicts failed entries.
   ​
3. **“Context is not used” is inaccurate.**  
    `AgentLoop._build_state_block()` already builds a context registry, sets the active thread-local registry, and injects a formatted state block. Desktop tools already use the registry for references and aliases.
   ​
4. **“Phase 7 needs another foreground poller” is wrong.**  
    `WatcherStateService._emit_events()` already diffs snapshots and publishes app/window/clipboard/settings events. Adding a second poller would duplicate events, waste resources, and inflate habit counts.
   ​
5. **“Phase 8 has no data path” is inaccurate.**  
    Phase 8 already has a memory action provider, boot-time aggregation, and a Phase 8 → Phase 7 habit-provider connection. The real issue is data quality and idempotency, not total absence of wiring.
   ​
6. **The repository currently registers far more than 23 tools.**  
    The implementation must always use `registry.names()` and tool metadata instead of assuming a fixed count.
   ​

### 2.3 Dangerous changes that must not be made

​

1. **Do not synchronously sleep and call `verify_live()` inside the main AgentLoop after every tool.**  
    This would block voice/chat latency, duplicate the existing background checker, and can produce two verdicts for one action.
   ​
2. **Do not add a second cache lookup inside AgentLoop.**  
    Cache lookup and replay already occur in `ChatService._run_agent()`. A second lookup creates two execution authorities and can execute one request twice.
   ​
3. **Do not replay only `cached.plan[0]`.**  
    A verified plan is an atomic ordered execution. Replaying only the first step is incorrect and dangerous.
   ​
4. **Do not call the planner with `confirmed=True` automatically.**  
    This bypasses confirmation. Confirmation must be explicit and tied to the exact action that the user approved.
   ​
5. **Do not treat tool return text as proof of success.**  
    `registry.execute()` returning without `ERROR` means the tool call was accepted. It does not always prove that the external effect happened.
   ​
6. **Do not add `um.observe_action("chat", ...)` after every response.**  
    General conversation is not an executed action. This would pollute the user model and duplicate existing memory aggregation.
   ​
7. **Do not lower a global learning threshold to a hardcoded value such as `2`.**  
    Promotion policy should depend on risk, evidence strength, determinism, context dependence, and verifier reliability—not one universal number.
   ​
8. **Do not inject the complete raw context snapshot into prompts.**  
    It creates token noise and may expose clipboard text, titles, paths, or sensitive state. Use a relevance-filtered and redacted context view.
   ​

---

​

## 3. Current Architecture That Must Be Preserved

​
Before editing, verify these integrations exist in the working branch. If the coding branch is older, port them before continuing.
​

### Execution correlation

​

- Every normal agent execution has an `execution_id`.
- Every tool call has an `action_id`.
- `Phase4Coordinator.publish_action_done()` carries both IDs.
- `Phase4Coordinator.publish_execution_completed()` publishes the complete manifest.
- Phase 6 joins asynchronous verdicts with the execution manifest.
  ​

### Verified cache behavior

​

- Exact normalized lookup is implemented.
- Safe atomic entries replay as `kind="tool"`.
- Complete multi-step entries replay as `kind="plan"`.
- Every replayed step is executed sequentially.
- Dangerous cached actions are not silently replayed.
- Failed replay invalidates the entry.
- `UNKNOWN`, incomplete, context-dependent, dangerous, and failed executions are not promoted.
  ​

### Context behavior

​

- AgentLoop builds a state block through the context registry.
- Context reference resolution is thread-local.
- Desktop aliases are learned generically rather than through app-name conditions.
  ​

### Proactive behavior

​

- Watcher snapshots already produce state-difference events.
- Phase 7 consumes events and remains suggestion-first.
- Phase 8 can provide trusted habits to Phase 7.
  ​

### Observability behavior

​

- Debug logs record execution/cache/verification lifecycle events.
- The Activity panel supports cache and verification events.
- `/api/activity/recent` exposes recent Phase 4 and Phase 6 background outcomes.
- Frontend assets use cache-busting/no-cache behavior so new activity event renderers are loaded.
  ​
  Do not replace these working paths with older snippets from the supplied document.
  ​

---

​

## 4. Target Architecture

​
Create one logical execution pipeline even if implementation remains split across modules:
​

```text
User request
  → Turn trace starts
  → Classification and route selection
  → Relevant context snapshot
  → Execution resolver
      → direct deterministic resolver, OR
      → verified cache, OR
      → planner/LLM tool resolution
  → confirmation policy
  → execution coordinator
      → action dispatch
      → action result
      → complete execution manifest
  → asynchronous verification
  → execution-level finalization
      → cache promotion/rejection/eviction
      → memory outcome write
      → learner update
      → user-model aggregation
  → suggestion engine (never silent auto-action by default)
  → Activity panel + debug logs + dashboard
```

​
There must be **one owner for execution**. ChatService may remain the request orchestrator, but tool execution should gradually move behind a reusable `ExecutionCoordinator` so normal agent tools, cached tools, cached plans, direct fast paths, and planner plans use the same lifecycle.
​

---

​

## 5. Canonical Data Contracts

​
Create explicit typed contracts, preferably dataclasses, in a new module such as:
​
`app/services/agent/execution/models.py`
​
Do not pass unrelated loose dictionaries forever. Serialization methods may still produce dictionaries for SSE and the event bus.
​

### 5.1 `ExecutionContext`

​
Required fields:
​

- `execution_id`
- `turn_id`
- `session_id`
- `user_message`
- `route`
- `source`: `direct`, `cache_tool`, `cache_plan`, `agent`, or `planner`
- `created_at`
- `context_fingerprint` containing only non-sensitive dependency facts
- `confirmation_grants`
  ​

### 5.2 `ActionSpec`

​
Required fields:
​

- `action_id`
- `tool`
- `args`
- `index`
- `depends_on`
- `risk_level`
- `requires_confirmation`
- `verification_capability`
- `context_dependencies`
- `fallback`, if validated
  ​

### 5.3 `ActionResult`

​
Required fields:
​

- `execution_id`
- `action_id`
- `tool`
- `args`
- `started_at`
- `finished_at`
- `transport_ok`
- `observation`
- `frontend_actions`
- `error_type`
- `error_message`
  ​
  Use `transport_ok` or `tool_ok`; do not call it verified success.
  ​

### 5.4 `VerificationResult`

​
Required fields:
​

- `execution_id`
- `action_id`
- `verdict`: `PASS`, `FAIL`, or `UNKNOWN`
- `reason`
- `evidence`
- `source`
- `confidence`
- `verified_at`
- `state_fingerprint`, when safe
  ​

### 5.5 `ExecutionManifest`

​
Required fields:
​

- execution metadata
- ordered action specs
- ordered action results
- expected verification IDs
- execution status
- confirmation history
- final response
- completion timestamp
  ​

### 5.6 Event schema version

​
Every cross-module event should contain:
​

- `schema_version`
- `event_id`
- `event_type`
- `occurred_at`
- `execution_id`, when applicable
- `action_id`, when applicable
  ​
  Version the schema now so future changes can be migrated without silently breaking subscribers.
  ​

---

​

## 6. Workstream A — Build a Shared Execution Coordinator

​

### Goal

​
Remove duplicated execution lifecycle code without performing a risky all-at-once rewrite.
​

### Files

​

- **NEW:** `app/services/agent/execution/__init__.py`
- **NEW:** `app/services/agent/execution/models.py`
- **NEW:** `app/services/agent/execution/coordinator.py`
- **MODIFY:** `app/services/agent/agent_loop.py`
- **MODIFY:** `app/services/chat_service.py`
- **MODIFY:** `app/services/agent/planner/executor.py`
  ​

### Steps

​

1. Implement `ExecutionCoordinator.execute_action(context, action_spec)`.
2. It must:
   - validate the tool exists;
   - validate arguments using the registry schema;
   - enforce risk/confirmation policy;
   - reset and collect the thread-local frontend action sink;
   - call the tool once;
   - build `ActionResult`;
   - record the memory action once;
   - publish `action.done` once;
   - emit structured debug/activity events through callbacks;
   - never perform verification synchronously on the request thread.
3. Implement `execute_plan(context, actions, stop_policy)`.
4. Execute steps in order and stop on a real tool failure.
5. Do not stop merely because verification is pending. For dependent steps that require verified state before continuing, mark the action metadata `verification_barrier=true` and wait only for those steps with a bounded timeout.
6. Preserve all current responses and SSE shapes while callers are migrated.
7. Migrate one caller at a time:
   - direct fast path;
   - cached atomic replay;
   - cached plan replay;
   - normal AgentLoop tool execution;
   - planner StepExecutor.
8. After all callers use the coordinator, remove duplicate calls to `registry.execute()`, `record_action()`, and `publish_action_done()` from callers.
   ​

### Acceptance criteria

​

- One user request cannot execute the same action through two paths.
- Every executed tool produces exactly one action result event.
- Every execution produces exactly one completion manifest.
- Existing confirmation behavior remains active.
- Existing cache regression tests continue to pass.
  ​

---

​

## 7. Workstream B — Verification and Evidence Policy

​

### Goal

​
Make verification correct, extensible, and non-blocking.
​

### Files

​

- `app/services/agent/checker/checker.py`
- `app/services/agent/checker/coordinator.py`
- `app/services/agent/checker/models.py`
- verifier registration modules
- `config.py`
  ​

### Required changes

​

1. Replace family-name inference as the long-term primary mechanism with tool metadata:
   ​

```python
verification={
    "strategy": "state",
    "capability": "system.wifi.enabled",
    "settle_profile": "radio",
    "context_keys": ["wifi.enabled"],
}
```

​
Family inference may remain as backward-compatible fallback while tools migrate.
​ 2. Add settle profiles rather than hardcoding command/tool names:

- `instant`
- `ui`
- `process`
- `radio`
- `network`
- `external`
  ​
  Each profile defines initial delay, polling interval, timeout, and maximum reads.
  ​

3. Keep verdict semantics strict:
   - `PASS`: evidence matches the requested target.
   - `FAIL`: evidence contradicts the target or the tool returned an error.
   - `UNKNOWN`: verifier missing, evidence unavailable, target ambiguous, or timeout without a definitive state.
     ​
4. Never convert `UNKNOWN` to `PASS` because the tool returned friendly text.
5. Vision fallback must be opt-in by tool metadata or supported action family. It must have rate limits, timeout, and explicit source labeling.
6. Store compact evidence. Never persist screenshots, clipboard content, tokens, or full window text by default.
7. Add verification metrics:
   - total by verdict;
   - total by source;
   - latency percentiles;
   - timeout count;
   - disagreement count between tool result and verifier;
   - missing-verifier count by tool.
     ​

### Frontend/browser action acknowledgement

​
Server-side success for `open_website`, generated content, browser playback, and other frontend actions may mean only that an instruction was emitted. Add an acknowledgement path:
​

1. Give each frontend action a `dispatch_id` and `action_id`.
2. Browser `handleActions()` sends an acknowledgement to a new endpoint after attempting the action.
3. Acknowledgement fields:
   - `dispatch_id`
   - `action_id`
   - `attempted`
   - `accepted`
   - safe error category
   - timestamp
4. Treat acknowledgement as transport evidence, not proof that an external website fully loaded.
5. Show acknowledgement in logs and Activity panel.
6. Do not cache browser-dependent actions unless the promotion policy accepts the available evidence.
   ​

---

​

## 8. Workstream C — Verified Command Cache

​

### Goal

​
Keep the cache fast without allowing semantic or contextual mistakes.
​

### Files

​

- `app/services/agent/cache/command_cache.py`
- `app/services/agent/cache/coordinator.py`
- `app/services/chat_service.py` during migration
- `config.py`
- database migration helper
  ​

### Required behavior

​

1. Keep exact normalized lookup as the only automatic-execution lookup until semantic safety is fully implemented.
2. A cache entry must store:
   - schema version;
   - kind;
   - ordered action graph;
   - tool schema/version fingerprint;
   - risk snapshot;
   - verification policy snapshot;
   - context dependency keys;
   - safe context fingerprint;
   - evidence class;
   - promotion timestamp;
   - last verified timestamp;
   - hits and failures.
3. Invalidate or quarantine an entry when:
   - a referenced tool no longer exists;
   - its argument schema changes;
   - risk metadata becomes stricter;
   - verification policy changes incompatibly;
   - replay fails;
   - replay verification returns FAIL;
   - required context no longer matches.
4. Do not cache:
   - dangerous actions for silent replay;
   - commands with unresolved references such as “it,” “that,” or “again” unless all reference dependencies are captured safely;
   - actions containing secrets;
   - non-deterministic external transactions;
   - incomplete plans;
   - any execution with FAIL or UNKNOWN under a strict promotion policy.
5. Plan replay must preserve order and stop on tool failure. Never replay only the first step.
6. Confirmation requirements must be recalculated at replay time from current tool metadata. Old cached metadata cannot waive a new safety rule.
   ​

### Promotion policy

​
Do not use one hardcoded success count. Implement a policy interface:
​

```text
promotion_decision = policy.evaluate(
  risk,
  determinism,
  evidence_strength,
  verifier_reliability,
  context_dependencies,
  complete_execution,
  previous_observations
)
```

​
Suggested default:
​

- deterministic + low risk + strong state verifier + complete PASS: may promote after one execution;
- weak/transport-only evidence: require repeated consistent evidence or do not auto-promote;
- context-dependent action: store only with validated context dependencies;
- dangerous/irreversible action: never silently replay;
- UNKNOWN: never promote.
  ​
  Make thresholds configuration-driven and category-driven, not command-driven.
  ​

### Semantic cache

​
`CACHE_SEMANTIC_ENABLED` currently suggests a feature that is not safe to use for direct execution unless implemented carefully.
​
Implement semantic matching in two stages:
​

1. Retrieval only: semantic search proposes candidates.
2. Safety gate: a structured equivalence check confirms that intent, entities, arguments, polarity, scope, and context dependencies match.
   ​
   Until the safety gate exists and has adversarial tests, semantic matches must never execute automatically. They may only assist the planner/LLM.
   ​

---

​

## 9. Workstream D — Planner Integration

​

### Goal

​
Use planning for real multi-step work without duplicating execution or bypassing confirmation.
​

### Current state

​
Phase 5 already exists and is reachable through the registered `do_multistep` tool. The current planner validates tool names, marks registry-dangerous tools as risky, supports preconditions/fallbacks, and has a StepExecutor.
​

### Do not do

​

- Do not call `p5.run(text, confirmed=True)` before every LLM request.
- Do not execute a plan during classification.
- Do not allow the planner and AgentLoop to both execute the same request.
- Do not treat an empty plan as success.
  ​

### Required improvements

​

1. Separate planning from execution:
   - `planner.make_plan()` is side-effect free.
   - `ExecutionCoordinator.execute_plan()` owns execution.
2. Define a validated Plan IR with:
   - ordered action IDs;
   - dependencies;
   - tool/argument validation;
   - risk metadata from registry, never only from LLM output;
   - preconditions;
   - fallback references;
   - verification barriers;
   - maximum step/cost limits.
3. Add a routing policy that selects planning when structural evidence indicates multiple or dependent actions. Do not maintain a growing phrase list.
4. The LLM may propose a plan, but the registry and policy engine must validate every step.
5. Confirmation must be action-scoped. A confirmation grant should bind:
   - execution ID;
   - action ID;
   - tool;
   - normalized argument hash;
   - expiry time.
6. Confirmation for one risky step must not authorize every later risky step.
7. On confirmation, resume the paused plan from persisted in-memory execution state. Do not rerun already completed steps.
8. If the process restarts, discard pending confirmation state safely unless durable resume is explicitly designed.
9. Planner fallback must not claim success from an unverified fallback. Existing fallback behavior that treats “no FAIL and no ERROR” as recovered should be tightened: use `UNVERIFIED` unless policy explicitly allows transport-only completion.
10. Publish the same execution/action contracts as every other path so cache and learning receive one consistent manifest.
    ​

### Acceptance criteria

​

- Two-step requests execute each step once and in order.
- A failed required step stops dependent steps.
- An independent action may continue only if the plan explicitly marks it independent.
- Confirmation resumes rather than restarts.
- Complete all-PASS plans can become `kind="plan"` cache entries.
  ​

---

​

## 10. Workstream E — Relevant Context, References, and Privacy

​

### Goal

​
Use context to improve decisions without prompt pollution or accidental disclosure.
​

### Files

​

- `app/services/context/context_engine.py`
- `app/services/agent/agent_loop.py`
- context providers and alias store
  ​

### Required changes

​

1. Keep the existing context registry and thread-local handoff.
2. Add a generic `get_relevant_context(query, tool_candidates=None)` method.
3. Context providers should declare:
   - capability keys;
   - sensitivity level;
   - freshness timestamp;
   - cost;
   - whether values are prompt-safe, log-safe, cache-safe, or verifier-only.
4. Select context by capability and candidate tool metadata—not application-name conditions.
5. Add freshness checks. Unknown/stale values must be labeled, not silently reused.
6. Never inject clipboard content into the LLM, cache key, or logs by default.
7. Redact secrets, tokens, long opaque strings, emails when unnecessary, and private paths.
8. Cache entries should capture only dependencies such as:
   - active application identity required for a relative action;
   - resolved target identifier;
   - relevant setting state.
9. Do not include unrelated state in a cache key. Over-specific keys destroy hit rate; under-specific keys cause wrong replay.
10. Add tests for pronouns, aliases, active-app references, stale context, and concurrent request isolation.
    ​

---

​

## 11. Workstream F — Memory and User Model Integrity

​

### Goal

​
Learn from verified behavior exactly once.
​

### Current risk

​
The user model’s default provider reads the complete `actions` table. Boot-time aggregation can count old rows again unless ingestion is idempotent. Repeated startup must not increase habit confidence without new actions.
​

### Required changes

​

1. Extend the memory `actions` schema with:
   - `execution_id`
   - `action_id`
   - `verification_verdict`
   - `verification_source`
   - `context_key`
   - optional safe target hash
2. Add a unique constraint or deduplication key for `action_id`.
3. Record initial action results once, then update the row when verification arrives.
4. UserModel must ingest only rows it has not processed. Use one of:
   - a persisted `last_action_id` watermark; or
   - an ingestion table keyed by source action ID.
5. Do not learn habits from:
   - failed actions;
   - UNKNOWN outcomes under strict policy;
   - general chat messages;
   - startup briefing prompts;
   - repeated aggregation of the same row;
   - cached replay counted as a new preference unless product policy explicitly chooses that behavior.
6. Preserve privacy controls and `forget_all()`.
7. Add a migration that is idempotent and backs up the database before schema changes.
8. Expose dashboard counters:
   - new actions ingested;
   - duplicates skipped;
   - unverified actions ignored;
   - trusted habits;
   - last aggregation cursor.
     ​

### Acceptance criteria

​

- Restarting JARVIS five times with no new actions does not change habit counts.
- One verified action is counted once.
- A failed or UNKNOWN action does not become a trusted habit.
  ​

---

​

## 12. Workstream G — Proactive Engine

​

### Goal

​
Provide useful suggestions without annoying or unsafe autonomous behavior.
​

### Required changes

​

1. Do not add another active-window poller. Reuse WatcherStateService events.
2. Deduplicate events using event type, stable state fingerprint, and a time window.
3. Add cooldowns per suggestion class and per context.
4. Require minimum confidence and minimum observations before suggesting a learned habit.
5. Suggestions must include a reason the user can understand.
6. Default behavior remains suggestion-only.
7. Never auto-execute dangerous, destructive, privacy-sensitive, financial, communication, or external side-effect actions.
8. If future auto-action is enabled, use an explicit allowlist of risk categories/capabilities—not command phrases—and maintain a global kill switch.
9. Feedback such as accept, dismiss, or “never suggest this” should update suggestion policy separately from action habits.
10. Do not infer a preference merely because JARVIS itself repeated a cached action.
    ​

---

​

## 13. Workstream H — End-to-End Observability

​

### Goal

​
A developer should reconstruct what happened without reading raw source or guessing.
​

### One structured event vocabulary

​
Use consistent events across debug files, Activity panel, and dashboard:
​

- `turn.started`
- `route.selected`
- `context.built`
- `cache.lookup`
- `cache.hit`
- `cache.miss`
- `plan.created`
- `confirmation.required`
- `confirmation.granted`
- `execution.started`
- `action.started`
- `action.dispatched`
- `frontend.acknowledged`
- `action.completed`
- `verification.queued`
- `verification.completed`
- `execution.completed`
- `cache.promoted`
- `cache.rejected`
- `cache.evicted`
- `memory.recorded`
- `habit.updated`
- `suggestion.created`
- `turn.completed`
- `turn.failed`
  ​

### Logging rules

​

1. Every event includes turn, execution, and action IDs when applicable.
2. Debug logs may be detailed; Activity panel must remain concise.
3. Log arguments through a redaction function.
4. Never log API keys, auth headers, passwords, OTPs, raw clipboard contents, full screenshots, or unrestricted email/calendar content.
5. Avoid duplicate events. One lifecycle transition should have one canonical producer.
6. Log explicit rejection reasons, including:
   - dangerous action;
   - context dependent;
   - incomplete manifest;
   - missing verdict;
   - UNKNOWN verdict;
   - failed step;
   - tool schema changed;
   - confirmation missing.
7. One conversation may have multiple turn logs, but every log must include session ID and turn ID. Provide a simple index or filename pattern that makes the order obvious.
8. Empty assistant responses must create a `turn.failed` or `turn.completed` record showing chunk count and error/cancellation reason.
9. `/api/activity/recent` must stay lightweight, bounded, and quiet in request logs.
10. Frontend asset cache busting must remain so newly added event labels appear after upgrades.
    ​

### Activity panel UX

​
Show meaningful events only:
​

```text
Route: Task
Cache: Miss
Plan: 2 steps
Action 1/2: set_wifi
Result: accepted
Verification: PASS
Action 2/2: set_volume
Verification: PASS
Cache: Plan promoted
Completed in 4.2s
```

​
Do not display internal polling attempts, every state read, SQL statements, stack traces, or secret-bearing raw data.
​

---

​

## 14. Database Migrations and Backward Compatibility

​

1. Add a small migration framework or schema-version table for:
   - memory database;
   - command cache database;
   - user model database.
2. Every migration must:
   - be idempotent;
   - run inside a transaction where SQLite permits;
   - create a timestamped backup before destructive changes;
   - preserve existing user data;
   - log start, success, and failure;
   - fail soft by disabling only the affected optional feature.
3. Do not silently reinterpret old cache payloads. Validate version and quarantine incompatible entries.
4. Do not delete user data during normal startup.
5. Add migration tests using copies of representative old database schemas.
   ​

---

​

## 15. Testing Strategy

​
Do not use real Wi-Fi, shutdown, email sending, file deletion, or browser side effects in automated tests. Use fake registries, fake watcher state, fake clocks, temporary SQLite databases, and deterministic event delivery.
​

### Unit tests

​

- execution/action ID creation and propagation;
- argument validation;
- risk and confirmation policy;
- context redaction/relevance;
- PASS/FAIL/UNKNOWN semantics;
- settle profile timing with fake clock;
- cache normalization;
- cache invalidation after tool schema change;
- memory ingestion watermark;
- proactive event deduplication.
  ​

### Integration tests

​

1. Safe atomic command:
   - first execution → tool called once;
   - action.done published once;
   - PASS received;
   - execution completed;
   - cache promoted;
   - second execution → cache hit;
   - replay still verified.
2. Multi-step command:
   - complete ordered manifest;
   - out-of-order verdict arrival;
   - promotion only after all expected PASS verdicts.
3. One UNKNOWN verdict:
   - execution not promoted.
4. One FAIL verdict:
   - existing matching entry evicted/quarantined.
5. Incomplete manifest:
   - never promoted, stale pending state eventually cleaned.
6. Dangerous action:
   - confirmation required;
   - no cache bypass;
   - grant bound to exact action/args;
   - no authorization of unrelated later actions.
7. Frontend action:
   - dispatch event sent;
   - acknowledgement correlated;
   - missing acknowledgement times out to UNKNOWN/transport-unconfirmed.
8. General/realtime/startup routes:
   - debug turn starts and closes;
   - empty response records a reason.
9. Restart idempotency:
   - user-model habit counts unchanged without new actions.
10. Concurrency:

- two sessions do not mix action sinks, IDs, logs, pending verdicts, or confirmations.
  ​

### Adversarial semantic-cache tests

​
Semantic retrieval must not equate:
​

- “turn Wi-Fi on” with “turn Wi-Fi off”;
- “close Notepad” with “open Notepad”;
- “delete file A” with “delete file B”;
- “send email to A” with “draft email to A”;
- “volume 20” with “volume 80”;
- “this window” in one context with “this window” in another context.
  ​

### Required validation commands

​
At minimum:
​

```bash
python -m compileall -q app tests scripts run.py config.py
python -m unittest -v tests.test_command_cache_execution
node --check web/script.js
```

​
Add targeted suites for the new execution coordinator, migrations, user-model idempotency, planner confirmation resume, and frontend acknowledgements.
​

---

​

## 16. Implementation Order and Commit Boundaries

​
Do not implement all workstreams in one giant edit.
​

### Stage 0 — Baseline and safety net

​

- Create branch and database backups.
- Run current tests and compile checks.
- Record current dashboard/Activity behavior.
- Confirm current execution-level cache changes are present.
  ​
  **Exit gate:** existing tests green.
  ​

### Stage 1 — Contracts only

​

- Add typed execution models and schema-versioned event helpers.
- Add serialization/redaction tests.
- Do not alter runtime execution yet.
  ​
  **Exit gate:** no behavior change.
  ​

### Stage 2 — ExecutionCoordinator

​

- Migrate direct path and cached replay first.
- Then AgentLoop.
- Then planner StepExecutor.
- Remove duplicate execution writes only after tests prove parity.
  ​
  **Exit gate:** one execution authority and no duplicate action events.
  ​

### Stage 3 — Verification metadata and browser acknowledgement

​

- Add tool verification metadata and settle profiles.
- Add frontend dispatch acknowledgement.
- Keep legacy verifier inference as fallback.
  ​
  **Exit gate:** every action has an honest PASS/FAIL/UNKNOWN path.
  ​

### Stage 4 — Cache policy and migration

​

- Add payload/schema fingerprints and context dependencies.
- Implement invalidation and quarantine.
- Keep semantic auto-execution disabled.
  ​
  **Exit gate:** safe exact replay and migration tests green.
  ​

### Stage 5 — Planner resume and scoped confirmation

​

- Separate planning from execution.
- Persist pending in-memory execution state.
- Resume from the exact pending action.
  ​
  **Exit gate:** no rerun of completed steps and no broad confirmation bypass.
  ​

### Stage 6 — Memory/user-model idempotency

​

- Add action IDs/verdict fields.
- Add ingestion cursor or deduplication table.
- Backfill safely without artificially increasing habit counts.
  ​
  **Exit gate:** repeated restart test passes.
  ​

### Stage 7 — Context and proactive refinement

​

- Relevance/redaction API.
- Event deduplication and suggestion feedback.
- No new watcher poller.
  ​
  **Exit gate:** privacy and noise tests pass.
  ​

### Stage 8 — Observability and final cleanup

​

- Unify event vocabulary.
- Ensure logs, Activity panel, and dashboard agree.
- Remove obsolete duplicated code after coverage proves it is unused.
  ​
  **Exit gate:** full acceptance checklist passes.
  ​
  Each stage should be a separate reviewable commit. Do not mix database migrations with unrelated UI styling or broad refactors.
  ​

---

​

## 17. Configuration Policy

​
Add configuration only for real policies, not to hide broken logic.
​
Recommended policy groups:
​

- execution timeouts;
- verification settle profiles;
- maximum plan steps/cost;
- confirmation grant expiry;
- cache promotion policy by risk/evidence class;
- pending execution retention;
- event queue size;
- Activity history limits;
- proactive confidence/cooldown;
- privacy/redaction flags.
  ​
  Validate values at startup. Clamp unsafe values. Log the effective policy once without logging secrets.
  ​
  Do not create command-specific configuration such as `YOUTUBE_CACHE=true` or `NOTEPAD_VERIFY_DELAY=2`.
  ​

---

​

## 18. Failure and Recovery Rules

​

- If context fails: continue with empty context.
- If cache fails: continue through normal resolution.
- If planner fails: fall back to AgentLoop.
- If checker fails: mark UNKNOWN; do not claim verified success.
- If memory fails: continue execution and log the optional subsystem failure.
- If Activity polling fails: chat must continue.
- If a frontend acknowledgement is missing: mark transport confirmation unavailable; do not fabricate success.
- If migration fails: preserve backup, disable only that optional subsystem, and show health status.
- If event delivery is dropped during shutdown: do not promote incomplete executions.
  ​
  User-facing language can remain concise, but internal records must preserve the real state.
  ​

---

​

## 19. Definition of Done

​
The implementation is complete only when all of the following are true:
​

- [ ] Every tool path uses the shared execution lifecycle.
- [ ] Every action has unique correlation IDs.
- [ ] Exactly one action result is published per tool execution.
- [ ] Complete execution manifests include every expected action.
- [ ] Verification remains asynchronous except explicit bounded barriers.
- [ ] PASS means evidence matched the requested target.
- [ ] UNKNOWN never silently becomes PASS.
- [ ] Tool and plan cache entries promote only under policy.
- [ ] Cached plan replay executes every validated step in order.
- [ ] Dangerous actions always re-evaluate confirmation requirements.
- [ ] Confirmation is scoped to action and arguments.
- [ ] Semantic similarity alone cannot trigger execution.
- [ ] Context is relevant, fresh, redacted, and dependency-aware.
- [ ] Memory actions are idempotent.
- [ ] Restarting does not inflate habits.
- [ ] Proactive behavior uses existing watcher events and defaults to suggestions.
- [ ] General, realtime, task, direct, cache, planner, camera, and startup routes close their debug traces.
- [ ] Activity panel displays direct path, cache, tools, verification, and final outcome.
- [ ] Logs contain useful rejection/error reasons without secrets.
- [ ] Migrations preserve existing data and have rollback backups.
- [ ] Unit, integration, adversarial, migration, concurrency, compile, and JavaScript checks pass.
      ​

---

​

## 20. Final Instruction to the Coding Agent

​
First inspect the current branch and map each requirement to the existing implementation. Do not assume the attached older analysis is authoritative. Preserve working execution-level cache correlation, current context integration, watcher event emission, and Phase 8 → Phase 7 wiring.
​
Implement generic contracts and policies before adding behavior. Prefer one reusable execution mechanism over code copied into every route. Never make a test pass by checking a particular command string, app name, website, or Hindi/English phrase. Add tool metadata or a reusable capability abstraction instead.
​
At the end of every stage, provide:
​

1. Files changed.
2. Why each change was necessary.
3. Data/schema migration performed.
4. Tests added.
5. Exact validation output.
6. Known limitations.
7. Rollback steps.
   ​
   Do not claim success only because code compiles. Demonstrate the complete lifecycle with deterministic tests and one manual Windows smoke-test matrix covering atomic execution, cache hit, multi-step plan, confirmation, failure, UNKNOWN verification, frontend action acknowledgement, general chat, and startup briefing.
