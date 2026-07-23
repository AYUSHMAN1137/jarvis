# JARVIS — Phase 3: Context Engine (Grounding Layer)

> **Status:** Design locked ✅ · Implementation: PENDING (user confirm ke baad)
> **Date:** 18 Jun 2026
> **Depends on:** Phase 1 (Watcher) ✅, Phase 2 (Memory) ✅
> **Part of:** JARVIS — Master Smartness Plan

---

## 0. Ye phase kya hai (one line)

JARVIS ki har baat ko **abhi ki reality (Watcher) + history (Memory) + conversation** se "ground" karna — taaki koi bhi reference ("isko band karo", "wo report bhejo", "jo file abhi banayi", "pehla wala kholo") ek **concrete cheez** se jud jaye, aur JARVIS guess na kare.

Goal sirf "isko/ye/wo" samajhna **nahi** hai — ek poora **Context Engine** banana hai jiski capability use ke saath badhti jaye (Master Plan ki vision).

---

## 1. Code analysis — abhi kya hai (important findings)

### 1.1 Do execution paths
| Path | File | Memory inject? | Watcher state? | History |
|------|------|----------------|----------------|---------|
| Normal chat | `groq_service.py` (`augment_system_prompt`) | ✅ haan | ❌ nahi | 20 turns (`MAX_CHAT_HISTORY_TURNS`) |
| **Agent / tool path** | `agent_loop.py` (`_AGENT_SYSTEM_PROMPT`, `_build_messages`) | ❌ **nahi** | ❌ **nahi** | sirf **last 6 turns** (`chat_history[-6:]`) |

> **🔑 KEY GAP:** Jo hissa actual **tools chalata hai** (`agent_loop`) wahi abhi **andha** hai — use na memory dikhti hai, na live system state. Isiliye "isko band karo" sirf `close_application` ke andar ek ad-hoc hack se chalta hai.

### 1.2 Jo already maujood hai (reuse karenge)
- **Watcher** `get_watcher().get_state()` deta hai: `active_window`, `windows`, `windows_detail`, `launched` (apps with name/pids/exe/opened_at, most-recent LAST), `clipboard_preview`, `settings` (wifi/bt/volume/brightness).
- **Memory** `get_prompt_context()` / `record_action()` / `record_correction()` / `recall()` — last action target + corrections + FTS5 search.
- **Ad-hoc anaphora** `desktop_tools.py` me `_ANAPHORA`-style set (`it/this/that/current/active/...`) + `close_by_name` me "it/this/isko → last opened" — **ye logic generalize hoga**.
- **Tool results** ek run ke andar already `messages` me append hote hain (`agent_loop`), to within-run base ready hai.

---

## 2. Architecture

```
  Watcher  ──┐
  Memory   ──┤
  Convo    ──┤──>  [ ENTITY REGISTRY ]  ──>  resolve(ref, type_hint)  ──adaptive──>  tool ko concrete handle
  Tools    ──┘        (live + ranked)              │
                                            confidence low? → JARVIS chhota sawaal poochhe
                                            user accept/correct? → memory me seekhe (salience++)
```

Do layer, dono saath (Hybrid):
- **Layer A (LLM):** har turn ek compact **ranked CONTEXT block** prompt me inject → LLM khud arg bhare.
- **Layer B (deterministic):** `resolve()` helper jise tools call karein → registry lookup + salience + confidence + clarify.

---

## 3. Entity model

```python
@dataclass
class ContextEntity:
    type: str          # app | window | file | selection | clipboard | url | setting | tool_result | person
    label: str         # "WhatsApp", "report.pdf", ...
    aliases: list[str] # ["whatsapp", "wa", "chat app"]
    source: str        # watcher | memory | conversation | tool_result
    handle: dict       # {"pid": 1234} / {"path": "C:/.."} / {"title": ".."} / {"text": ".."} / {"url": ".."}
    first_seen: float
    last_seen: float
    last_referenced: float
    salience: float    # computed: recency + focus + frequency + explicit-mention
```

---

## 4. Sources + tracking rules (DECIDED)

| Source | Kya feed hota hai | Rule |
|--------|-------------------|------|
| **Watcher** | active_window, open windows, launched apps, clipboard, settings, (aage: desktop files) | live cheezein **hamesha referenceable** jab tak khuli hain (turn-limit nahi) |
| **Conversation** | last N turns me mention hui cheezein | **~6 turns** tak, phir salience decay |
| **Tool results** | har tool ka notable output (search results, created/opened file, web result) | **ADVANCED** — results + content store, content me search bhi |
| **Memory** | last action target, learned aliases, frequent items | always available |

> **Hybrid turn rule:** *Mention* ~6 turns me decay hota hai, lekin *live* cheez (open app/window) jab tak khuli hai tab tak referenceable — chaahe 15 turn pehle khuli ho.

---

## 5. Resolution pipeline (adaptive — DECIDED)

```
user: "ise band karo"
   │
   1. TYPE-HINT      verb se: band→app/window, delete→file, bhejo→file/text, kholo→file/url/app
   │
   2. CANDIDATES     registry se compatible-type entities nikaalo
   │
   3. SALIENCE RANK  score = recency + focus(active window?) + recent-mention + verb-type-match + frequency
   │
   4. CONFIDENCE     top score clearly aage? → HIGH → khud resolve (guess)
   │                 do candidate paas-paas? → LOW → chhota sawaal: "WhatsApp ya Chrome?"
   │
   5. RESOLVE        chosen entity.handle → tool ko de do
```

- **High confidence → khud guess** (fast).
- **Low confidence → poochho** ("dont make mistakes").
- Threshold tunable (default: top score 2nd se >= X% aage = high).

---

## 6. Integration points (exact, code ke according)

1. **State block inject** → `agent_loop._build_messages()` / system prompt assembly (line ~307-314). Yahan ek `[CURRENT STATE]` block add hoga (active window, recent apps, toggles, recent tool-results, last action).
2. **`resolve()` shared helper** → naya module `app/services/context/` (e.g. `context_engine.py`). `close_application` ki anaphora-logic isme shift + generalize hogi; phir `close_application`, `focus_window`, `window_action`, file/settings tools sab ise call karein.
3. **Registry feed** → har turn pe Watcher `get_state()` se rebuild + conversation parse + tool-result hook (`agent_loop` ke tool-dispatch loop, line ~366 ke baad result ko registry me daalo).
4. **Learning** → `memory_service.record_correction()` + naya `learned_aliases` table; salience boost on accept.

---

## 7. Learning (auto + confirm MIX — DECIDED)

- **Auto (chupchaap):** jis resolution ko user accept kare (correct na kare) → us entity ka salience future me thoda zyada. Koi pop-up nahi.
- **Confirm (pehli baar):** jab koi naya pakka alias banta dikhe ("mera doc" baar-baar ek hi file) → **ek baar** confirm: "'mera doc' = report.pdf, yaad rakhu?" → haan → permanent (SQLite `learned_aliases`).
- Ye data **future trainer-agent** ko feed karega (alag phase).

---

## 8. Reliability + Privacy (Master Plan cautions)

- **Fail-soft:** Context Engine kabhi exception na phenke; fail ho to JARVIS purane (current) behaviour pe chale. Crash kabhi nahi.
- **Privacy:** clipboard + selection preview **kabhi log nahi** (RAM only, capped) — Watcher already aise karta hai.
- **Light:** registry in-memory (har tick rebuild); sirf `learned_aliases` SQLite me. Voice latency na badhe.

---

## 9. Build order (chhote, testable chunks — ek saath NAHI)

| # | Chunk | Sandbox test (main) | Machine test (tum) |
|---|-------|---------------------|--------------------|
| 1 | Entity Registry + salience scoring | ✅ unit-test (fake state) | — |
| 2 | Resolver pipeline + adaptive confidence | ✅ unit-test | — |
| 3 | State block → `agent_loop` inject | ⚠️ py_compile only | ✅ live |
| 4 | `resolve()` shared helper → tools wire | ✅ logic + py_compile | ✅ live |
| 5 | Tool-result entities (advanced) + content search | ✅ unit-test | ✅ live |
| 6 | Learning hooks (auto + confirm) | ✅ logic + py_compile | ✅ live |

Har chunk ke baad: `py_compile` + logic test → tum machine pe verify → tabhi agla chunk.

---

## 10. Testing guide (jab implement ho jaye)

### A. Unit tests (main sandbox me chalaunga)
- Registry me fake entities daal ke salience ranking check.
- Resolver ko "isko/ye/wo/pehla wala" + fake state de ke sahi target nikalta hai ya nahi.
- Confidence: 2 paas-paas candidates → clarify trigger ho.

### B. Live tests (tum apni Windows machine pe)
1. **Live app reference:** Chrome kholo → kuch aur baatein karo (5-6 turn) → "isko band karo" → Chrome band hona chahiye.
2. **Multi-app ambiguity:** Chrome + Notepad dono khole → "ise band karo" → JARVIS poochhe "kaunsa?".
3. **Tool-result:** "python ke baare me search karo" → "pehla result kholo".
4. **File:** "ek file banao report.txt" → "ise delete karo" / "ise telegram pe bhejo".
5. **Learned alias:** "mera doc kholo" (naya) → confirm → agli baar bina poochhe khule.
6. **Fail-safe:** Watcher band karke dekho — JARVIS crash na ho, purane tareeke se chale.

---

## 11. Future hook — Trainer Agent (alag phase)

JARVIS ke andar ek **alag agent jo ise train karega** — Master Plan me alag phase add hoga (baad me discuss). Phase 3 ka **Entity Registry + correction/alias learning** us trainer ka data-foundation hai. Isiliye ye phase abhi se advanced level pe banaया ja raha hai.

---

## 12. Open / tunable later
- Salience weights (recency vs focus vs frequency) — real use se tune.
- Confidence threshold (kab poochhe vs guess).
- Tool-result content-search index (kitna bada, kab purge).

---

*Is doc ko Master Plan ke saath padho. Implementation user ke "go" ke baad, chunk-by-chunk.*


---

## ✅ Implementation Status (BUILT)

Phase 3 ab code me poori tarah implement ho chuka hai. Saare hisse fail-soft
hain (koi bhi error -> JARVIS purane behaviour pe chalta hai, kabhi crash nahi).

**Naya / badla code:**

| # | File | Kya hua |
|---|------|---------|
| 1 | `app/services/context/__init__.py` | Naya package (exports) |
| 2 | `app/services/context/context_engine.py` | **Naya** core: `ContextEntity`, `ContextRegistry`, `ResolveResult`, `AliasStore`, `detect_reference`, `infer_types_from_text`, `parse_ordinal`, `build_registry`, `set_active_registry`/`get_active_registry`, `get_alias_store`, `learn_alias` |
| 3 | `app/services/agent/agent_loop.py` | `_build_state_block()` + `_build_messages()` me **live STATE block inject**; har successful tool result `add_tool_result()` se registry me; thread-local registry handoff |
| 4 | `app/services/agent/tools/desktop_tools.py` | `_resolve_window_reference()` helper; `focus_window` & `window_action` ab vague refs ("this/isko/pehla wala") ko live context se resolve karte hain |
| 5 | `tests/test_context_engine.py` | **Naya** unit-test suite — 15 test groups, sab pass (pure-python, OS/LLM ke bina) |

**Chunk-wise (Master Plan ke according):**
- ✅ Chunk 1 — Context Engine core module
- ✅ Chunk 2 — Agent loop me live state-block injection ("blind agent" gap fixed)
- ✅ Chunk 3 — Mid-run tool-result tracking ("pehla wala / jo abhi nikla")
- ✅ Chunk 4 — Deterministic `resolve()` desktop tools (focus/window) me wired
- ✅ Chunk 5 — Tool-result **content search** (label hi nahi, andar ke text/url/path pe bhi match)
- ✅ Chunk 6 — Learned-alias READ + **conservative** safe-learn (`learn_alias` kabhi pronoun/ordinal/short/blank store nahi karta)

**Note on learning (reliability #1):** alias auto-learn jaan-boojh kar conservative
rakha hai — sirf genuine naam/alias (jaise "mera browser") store hote hain,
throwaway words ("isko", "pehla") kabhi nahi. "First-time confirm" UI aur active
training ka kaam future **trainer-agent phase** me hoga (Master Plan §11).

**Testing (sandbox-verified):**
```
python tests/test_context_engine.py      # -> ALL 15 TEST GROUPS PASSED
python -m py_compile app/services/context/context_engine.py \
  app/services/agent/agent_loop.py app/services/agent/tools/desktop_tools.py
```
Live Windows par testing ke liye is doc ka §10 follow karo.
