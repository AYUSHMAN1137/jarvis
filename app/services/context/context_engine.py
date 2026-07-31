"""
Phase 3 — Context Engine (grounding layer).

Vague references ko concrete entities se jodta hai by combining:
  - live system state (Watcher: active window, open apps, clipboard, settings)
  - recent conversation (mention boost)
  - recent tool results ("pehla wala", "jo file banayi")
  - long-term memory (last action target + learned aliases)

Design notes (Master Plan ke according):
  - Hybrid: ye module ek compact STATE block deta hai (LLM ke liye) AUR ek
    deterministic resolve() (tools ke liye).
  - Adaptive: high confidence -> khud resolve; low -> caller user se poochhe.
  - Fail-soft: koi bhi method exception ko andar hi nigal leta hai; caller
    apne purane behaviour pe chal sakta hai.
  - Light: registry har turn in-memory banti hai; sirf learned aliases SQLite.

Ye module PURE-PYTHON hai — koi live GUI/OS lib import nahi karta, isliye
sandbox me poori tarah unit-test ho sakta hai (fake state de ke).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("J.A.R.V.I.S")

# --------------------------------------------------------------------------- #
# Reference vocabulary
# --------------------------------------------------------------------------- #

# Words that mean "the thing in context" rather than a concrete name.
_PRONOUNS = {
    # English
    "it", "this", "that", "these", "those", "current", "active", "foreground",
    "the app", "the window", "the file", "the result",
    # Hindi (romanized)
    "isko", "ise", "is", "iska", "isme", "ye", "yah", "yeh", "yahi",
    "wo", "woh", "usko", "use", "uska", "usme", "wahi", "inko", "unko",
}

# Ordinal words -> zero-based index.
_ORDINALS: Dict[str, int] = {
    "first": 0, "1st": 0, "pehla": 0, "pehli": 0, "pahla": 0, "pehle": 0,
    "second": 1, "2nd": 1, "doosra": 1, "doosri": 1, "dusra": 1, "dusri": 1,
    "third": 2, "3rd": 2, "teesra": 2, "teesri": 2, "tisra": 2,
    "fourth": 3, "4th": 3, "chautha": 3,
    "fifth": 4, "5th": 4, "paanchwa": 4,
    "last": -1, "aakhri": -1, "akhri": -1, "last wala": -1,
}

# Verb / intent -> which entity types are plausible targets.
_VERB_TYPES: List[Tuple[Tuple[str, ...], Tuple[str, ...]]] = [
    (("close", "band", "quit", "exit", "kill", "shut", "minimize", "maximize",
      "focus", "switch", "window"), ("window", "app")),
    (("open", "kholo", "launch", "start", "chalao", "run"),
     ("app", "file", "url", "tool_result")),
    (("delete", "remove", "hatao", "mitao", "trash", "recycle"),
     ("file", "tool_result")),
    (("send", "bhejo", "share", "forward", "attach", "upload"),
     ("file", "tool_result", "url", "clipboard")),
    (("read", "padho", "summarize", "summarise", "open"),
     ("file", "tool_result", "clipboard", "url")),
    (("copy", "paste", "clipboard"), ("clipboard",)),
    (("on", "off", "toggle", "enable", "disable"), ("setting",)),
]

ALL_TYPES = (
    "app", "window", "file", "clipboard", "selection", "url",
    "setting", "tool_result", "person",
)

_WORD_RE = re.compile(r"[a-z0-9]+(?:\.[a-z0-9]+)*", re.I)


def _tokens(text: str) -> List[str]:
    return _WORD_RE.findall((text or "").lower())


def detect_reference(text: str) -> bool:
    """True agar text me koi vague reference (pronoun/ordinal) lagta hai."""
    t = (text or "").lower().strip()
    if not t:
        return False
    if any(p in _PRONOUNS for p in _tokens(t)):
        return True
    # multi-word pronouns / ordinals
    for phrase in ("the app", "the window", "the file", "the result", "last wala"):
        if phrase in t:
            return True
    if any(o in _tokens(t) for o in _ORDINALS):
        return True
    return False


def parse_ordinal(text: str) -> Optional[int]:
    """"pehla/doosra/first/last" -> zero-based index (last == -1). Else None."""
    t = (text or "").lower()
    if "last wala" in t or "last" in _tokens(t):
        # only treat as ordinal if it clearly refers to position
        for w in ("last", "aakhri", "akhri"):
            if w in _tokens(t):
                return -1
    for tok in _tokens(t):
        if tok in _ORDINALS:
            return _ORDINALS[tok]
    return None


def infer_types_from_text(text: str) -> Optional[Tuple[str, ...]]:
    """Verb se plausible target types. None = koi bhi type."""
    toks = set(_tokens(text))
    matched: List[str] = []
    for verbs, types in _VERB_TYPES:
        if toks.intersection(verbs):
            for ty in types:
                if ty not in matched:
                    matched.append(ty)
    return tuple(matched) if matched else None


# --------------------------------------------------------------------------- #
# Entity model
# --------------------------------------------------------------------------- #


@dataclass
class ContextEntity:
    type: str                       # one of ALL_TYPES
    label: str                      # human label ("WhatsApp", "report.pdf")
    handle: Dict[str, Any] = field(default_factory=dict)  # pid/path/title/text/url
    aliases: List[str] = field(default_factory=list)
    source: str = "watcher"         # watcher | conversation | tool_result | memory
    is_focus: bool = False          # active/foreground?
    order: int = 0                  # explicit ordering within a result set
    last_seen: float = 0.0
    opened_at: float = 0.0

    def matches_name(self, ref: str) -> bool:
        ref = (ref or "").strip().lower()
        if not ref:
            return False
        hay = [self.label.lower()] + [a.lower() for a in self.aliases]
        # Phase 3 content search: for result-like entities, also look INSIDE the
        # stored text/title/url/path so a reference can match by CONTENT, not
        # just the truncated label (e.g. "wo python wala link", "jo file banayi").
        # Guarded to ref length >= 3 so short tokens don't match huge blobs.
        if len(ref) >= 3 and self.type in ("tool_result", "file", "url", "clipboard"):
            for key in ("text", "title", "url", "path"):
                val = self.handle.get(key)
                if val:
                    hay.append(str(val).lower()[:300])
        for h in hay:
            if not h:
                continue
            if ref == h or ref in h or h in ref:
                return True
        return False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "label": self.label,
            "handle": dict(self.handle),
            "source": self.source,
            "is_focus": self.is_focus,
            "order": self.order,
        }


@dataclass
class ResolveResult:
    matched: bool = False
    entity: Optional[ContextEntity] = None
    confidence: str = "none"        # "high" | "low" | "none"
    candidates: List[ContextEntity] = field(default_factory=list)
    question: Optional[str] = None  # clarifying question when confidence == low

    def as_handle(self) -> Optional[Dict[str, Any]]:
        return self.entity.handle if self.entity else None


# --------------------------------------------------------------------------- #
# Learned-alias store (SQLite, fail-soft, in-memory fallback)
# --------------------------------------------------------------------------- #


class AliasStore:
    """Persistent map: phrase -> {"label", "type", "handle"} (learned over time).

    Apni SQLite connection rakhta hai (memory.db me ek alag table) taaki
    MemoryService ke saath koi clash na ho. Sab kuch fail-soft; DB na mile to
    in-memory dict pe chalega.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._lock = threading.RLock()
        self._mem: Dict[str, Dict[str, Any]] = {}
        self._conn = None
        if db_path is None:
            try:
                from config import MEMORY_DB_PATH
                db_path = str(MEMORY_DB_PATH)
            except Exception:  # noqa: BLE001
                db_path = None
        if db_path:
            try:
                # NOTE: this is the *same* file as memory.db. Two live
                # connections to one database mean a checkpoint can never
                # truncate the WAL until both are closed -- which is why both
                # go through the shared registry.
                from app.services.db import open_db
                self._conn = open_db(db_path, label="context_aliases")
                self._conn.execute(
                    """CREATE TABLE IF NOT EXISTS context_aliases(
                        phrase TEXT PRIMARY KEY,
                        label TEXT,
                        type TEXT,
                        handle TEXT,
                        hits INTEGER DEFAULT 1,
                        updated_at TEXT)"""
                )
                self._conn.commit()
            except Exception as e:  # noqa: BLE001
                logger.debug("[CONTEXT] alias store DB unavailable: %s", e)
                self._conn = None

    def get(self, phrase: str) -> Optional[Dict[str, Any]]:
        phrase = (phrase or "").strip().lower()
        if not phrase:
            return None
        try:
            with self._lock:
                if self._conn is not None:
                    cur = self._conn.execute(
                        "SELECT label, type, handle FROM context_aliases WHERE phrase=?",
                        (phrase,),
                    )
                    row = cur.fetchone()
                    if row:
                        import json
                        handle = {}
                        try:
                            handle = json.loads(row[2]) if row[2] else {}
                        except Exception:  # noqa: BLE001
                            handle = {}
                        return {"label": row[0], "type": row[1], "handle": handle}
                    return None
                return self._mem.get(phrase)
        except Exception as e:  # noqa: BLE001
            logger.debug("[CONTEXT] alias get failed: %s", e)
            return self._mem.get(phrase)

    def put(self, phrase: str, label: str, type_: str, handle: Dict[str, Any]) -> None:
        phrase = (phrase or "").strip().lower()
        if not phrase:
            return
        rec = {"label": label, "type": type_, "handle": handle or {}}
        try:
            with self._lock:
                self._mem[phrase] = rec
                if self._conn is not None:
                    import json
                    self._conn.execute(
                        """INSERT INTO context_aliases(phrase, label, type, handle, hits, updated_at)
                           VALUES(?,?,?,?,1,?)
                           ON CONFLICT(phrase) DO UPDATE SET
                             label=excluded.label, type=excluded.type,
                             handle=excluded.handle, hits=hits+1,
                             updated_at=excluded.updated_at""",
                        (phrase, label, type_, json.dumps(handle or {}),
                         time.strftime("%Y-%m-%dT%H:%M:%S")),
                    )
                    self._conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.debug("[CONTEXT] alias put failed: %s", e)

    def all(self) -> Dict[str, Dict[str, Any]]:
        try:
            with self._lock:
                if self._conn is not None:
                    import json
                    out: Dict[str, Dict[str, Any]] = {}
                    for phrase, label, type_, handle in self._conn.execute(
                        "SELECT phrase, label, type, handle FROM context_aliases"
                    ):
                        try:
                            h = json.loads(handle) if handle else {}
                        except Exception:  # noqa: BLE001
                            h = {}
                        out[phrase] = {"label": label, "type": type_, "handle": h}
                    return out
                return dict(self._mem)
        except Exception:  # noqa: BLE001
            return dict(self._mem)


# --------------------------------------------------------------------------- #
# Salience weights (tunable)
# --------------------------------------------------------------------------- #

_W_FOCUS = 5.0          # active/foreground window
_W_MENTION = 4.0        # mentioned in recent conversation
_W_RECENCY = 3.0        # how recently seen/opened (decayed)
_W_TOOLRESULT = 2.0     # fresh tool result bonus
_W_TYPEMATCH = 1.0      # entity type matches verb hint
_RECENCY_HALFLIFE = 120.0   # seconds; after this, recency weight halves


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


class ContextRegistry:
    """Live, ranked set of entities JARVIS can act on right now."""

    def __init__(self, now_fn: Callable[[], float] = time.time,
                 alias_store: Optional[AliasStore] = None) -> None:
        self._now = now_fn
        self._entities: List[ContextEntity] = []
        self._recent_text: str = ""
        self._aliases = alias_store

    # ---- population ---- #
    def add(self, entity: ContextEntity) -> None:
        if entity.last_seen <= 0:
            entity.last_seen = self._now()
        self._entities.append(entity)

    def add_conversation(self, turns: Optional[Sequence[Tuple[Any, Any]]],
                         max_turns: int = 6) -> None:
        """Store recent user/assistant text for mention-boost (no entity parse)."""
        if not turns:
            return
        recent = list(turns)[-max_turns:]
        parts: List[str] = []
        for u, a in recent:
            parts.append(str(u or ""))
            parts.append(str(a or ""))
        self._recent_text = " ".join(parts).lower()

    def build_from_state(self, state: Optional[Dict[str, Any]]) -> None:
        """Watcher get_state() dict se app/window/clipboard/setting entities banao."""
        if not state:
            return
        now = self._now()
        active = (state.get("active_window") or "").strip()
        # launched apps (most-recent LAST in watcher) -> app entities
        for item in (state.get("launched") or []):
            try:
                name = (item.get("name") or "").strip()
                if not name:
                    continue
                self.add(ContextEntity(
                    type="app",
                    label=name,
                    handle={"name": name, "pids": item.get("pids") or []},
                    source="watcher",
                    is_focus=bool(active and (name.lower() in active.lower())),
                    last_seen=now,
                    opened_at=float(item.get("opened_at") or now),
                ))
            except Exception:  # noqa: BLE001
                continue
        # open windows -> window entities
        for title in (state.get("windows") or []):
            title = (title or "").strip()
            if not title:
                continue
            self.add(ContextEntity(
                type="window",
                label=title,
                handle={"title": title},
                source="watcher",
                is_focus=bool(active and title == active),
                last_seen=now,
            ))
        # clipboard
        clip = (state.get("clipboard_preview") or "").strip()
        if clip:
            self.add(ContextEntity(
                type="clipboard", label="clipboard",
                handle={"text": clip}, source="watcher", last_seen=now,
            ))
        # settings/toggles
        for key, val in (state.get("settings") or {}).items():
            self.add(ContextEntity(
                type="setting", label=str(key),
                handle={"name": str(key), "value": val},
                source="watcher", last_seen=now,
            ))

    def add_tool_result(self, name: str, observation: str,
                        order: int = 0, handle: Optional[Dict[str, Any]] = None,
                        type_: str = "tool_result") -> None:
        obs = (observation or "").strip()
        if not obs:
            return
        self.add(ContextEntity(
            type=type_,
            label=obs[:80],
            handle=handle or {"text": obs, "tool": name},
            source="tool_result",
            order=order,
            last_seen=self._now(),
        ))

    def add_last_action_target(self, tool: str, target: str) -> None:
        target = (target or "").strip()
        if not target:
            return
        self.add(ContextEntity(
            type="app", label=target,
            handle={"name": target}, source="memory",
            last_seen=self._now() - 1,  # slightly older than live state
        ))

    # ---- scoring ---- #
    def _recency_factor(self, entity: ContextEntity) -> float:
        ref = max(entity.last_seen, entity.opened_at)
        if ref <= 0:
            return 0.0
        age = max(0.0, self._now() - ref)
        return 0.5 ** (age / _RECENCY_HALFLIFE)

    def _mentioned(self, entity: ContextEntity) -> bool:
        if not self._recent_text:
            return False
        names = [entity.label.lower()] + [a.lower() for a in entity.aliases]
        return any(n and n in self._recent_text for n in names)

    def score(self, entity: ContextEntity,
              type_hint: Optional[Sequence[str]] = None) -> float:
        s = 0.0
        if entity.is_focus:
            s += _W_FOCUS
        if self._mentioned(entity):
            s += _W_MENTION
        s += _W_RECENCY * self._recency_factor(entity)
        if entity.source == "tool_result":
            s += _W_TOOLRESULT
        if type_hint and entity.type in type_hint:
            s += _W_TYPEMATCH
        return s

    def ranked(self, type_hint: Optional[Sequence[str]] = None) -> List[ContextEntity]:
        scored = [(self.score(e, type_hint), e) for e in self._entities]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored]

    # ---- resolution ---- #
    _HIGH_MARGIN = 1.5   # top must beat 2nd by this to auto-pick
    _HIGH_FLOOR = 2.0    # ...and be at least this strong

    def resolve(self, reference: str,
                type_hint: Optional[Sequence[str]] = None) -> ResolveResult:
        """Reference ko entity se jodo. Adaptive confidence."""
        ref = (reference or "").strip().lower()
        if not ref:
            return ResolveResult(matched=False, confidence="none")

        # 0) learned alias direct hit (very high confidence)
        if self._aliases is not None:
            rec = self._aliases.get(ref)
            if rec:
                ent = ContextEntity(
                    type=rec.get("type") or "app",
                    label=rec.get("label") or ref,
                    handle=rec.get("handle") or {},
                    source="memory", aliases=[ref],
                )
                return ResolveResult(matched=True, entity=ent, confidence="high")

        if type_hint is None:
            type_hint = infer_types_from_text(ref)

        # candidate pool, type-filtered when we have a hint
        pool = list(self._entities)
        if type_hint:
            typed = [e for e in pool if e.type in type_hint]
            if typed:
                pool = typed

        if not pool:
            return ResolveResult(matched=False, confidence="none")

        # 1) ordinal ("pehla/doosra/last wala") over ordered result sets
        ord_idx = parse_ordinal(ref)
        if ord_idx is not None:
            ordered = [e for e in pool if e.type in ("tool_result", "window", "file", "url")]
            ordered.sort(key=lambda e: (e.order, -e.last_seen))
            if ordered:
                try:
                    pick = ordered[ord_idx]
                    return ResolveResult(matched=True, entity=pick, confidence="high")
                except IndexError:
                    pass

        # 2) direct name match (not a pure pronoun)
        is_pronoun = ref in _PRONOUNS or any(
            p in _PRONOUNS for p in _tokens(ref)
        ) or ref in ("the app", "the window", "the file", "the result")
        if not is_pronoun:
            named = [e for e in pool if e.matches_name(ref)]
            if len(named) == 1:
                return ResolveResult(matched=True, entity=named[0], confidence="high")
            if len(named) > 1:
                ranked_named = sorted(named, key=lambda e: self.score(e, type_hint),
                                      reverse=True)
                return self._decide(ranked_named, type_hint)

        # 3) pronoun / fuzzy -> salience ranking
        ranked = self.ranked(type_hint)
        return self._decide(ranked, type_hint)

    @staticmethod
    def _primary_token(label: str) -> str:
        toks = _tokens(label)
        return toks[0] if toks else (label or "").lower()

    def _decide(self, ranked: List[ContextEntity],
                type_hint: Optional[Sequence[str]]) -> ResolveResult:
        if not ranked:
            return ResolveResult(matched=False, confidence="none")
        top = ranked[0]
        top_score = self.score(top, type_hint)
        top_key = self._primary_token(top.label)
        # Compare against the next DISTINCT target, not a duplicate
        # representation of the same thing (e.g. the "notepad" app entity vs the
        # "Notepad - untitled" window entity should not look ambiguous).
        second_score = 0.0
        for e in ranked[1:]:
            if self._primary_token(e.label) != top_key:
                second_score = self.score(e, type_hint)
                break
        if top_score >= self._HIGH_FLOOR and (top_score - second_score) >= self._HIGH_MARGIN:
            return ResolveResult(matched=True, entity=top, confidence="high")
        # low confidence -> ask. Dedupe candidates by target for a clean question.
        cands: List[ContextEntity] = []
        seen: set = set()
        for e in ranked:
            k = self._primary_token(e.label)
            if k in seen:
                continue
            seen.add(k)
            cands.append(e)
            if len(cands) >= 3:
                break
        labels = [c.label for c in cands]
        question = "Konsa? " + " / ".join(labels) if labels else None
        return ResolveResult(matched=False, entity=top, confidence="low",
                             candidates=cands, question=question)

    # ---- prompt block ---- #
    def format_state_block(self, max_chars: int = 700, query: str = "") -> str:
        """Compact, relevance-filtered, prompt-safe live-state block."""
        if not self._entities:
            return ""
        lines: List[str] = []
        # Prefer the window title for the active-window line (more descriptive
        # than the bare app name); fall back to any focused entity.
        focus = (next((e for e in self._entities if e.is_focus and e.type == "window"), None)
                 or next((e for e in self._entities if e.is_focus), None))
        if focus:
            lines.append(f"[Active window] {focus.label}")
        query_tokens = set(_tokens(query))
        apps = [e for e in self._entities if e.type == "app"]
        apps.sort(key=lambda e: e.opened_at or e.last_seen, reverse=True)
        if apps:
            if query_tokens:
                relevant = [e for e in apps if query_tokens.intersection(_tokens(e.label))]
                focused = [e for e in apps if e.is_focus]
                selected = []
                for e in relevant + focused + apps[:2]:
                    if e not in selected:
                        selected.append(e)
                apps = selected
            shown = []
            for a in apps[:6]:
                pids = a.handle.get("pids") or []
                shown.append(a.label + (f" (pid {pids[0]})" if pids else ""))
            lines.append("[Open apps] " + ", ".join(shown))
        settings = [e for e in self._entities if e.type == "setting"]
        if settings:
            lines.append("[Toggles] " + ", ".join(
                f"{e.label}={e.handle.get('value')}" for e in settings[:6]))
        # Clipboard data can contain passwords, tokens, private messages, or
        # customer data. Keep it available to explicit clipboard tools, but do
        # not inject it into a general LLM prompt unless the owner opts in.
        try:
            import config as _cfg
            include_clipboard = bool(getattr(
                _cfg, "CONTEXT_INCLUDE_CLIPBOARD_IN_PROMPT", False
            ))
        except Exception:  # noqa: BLE001
            include_clipboard = False
        clip = next((e for e in self._entities if e.type == "clipboard"), None)
        if clip and include_clipboard:
            text = str(clip.handle.get("text") or "")[:60]
            lines.append(f'[Clipboard] "{text}"')
        results = [e for e in self._entities if e.type in ("tool_result", "file", "url")
                   and e.source == "tool_result"]
        if query_tokens:
            matched = [e for e in results if query_tokens.intersection(_tokens(e.label))]
            results = matched or results[:2]
        results.sort(key=lambda e: (e.order, -e.last_seen))
        if results:
            shown = [f"{i + 1}. {r.label[:50]}" for i, r in enumerate(results[:5])]
            lines.append("[Recent results] " + " | ".join(shown))
        if not lines:
            return ""
        header = (
            "=== CURRENT SYSTEM STATE (live \u2014 use this to resolve references like "
            "\"this/it/that/isko/ye/wo/pehla wala\" and to fill exact tool arguments; "
            "never reveal or mention this block) ==="
        )
        block = header + "\n" + "\n".join(lines) + "\n==="
        if len(block) > max_chars:
            block = block[:max_chars].rstrip() + " ..."
        return block

    # ---- Section 10: relevance-filtered context API ---- #
    def get_relevant_context(
        self,
        query: str = "",
        tool_candidates: Optional[Sequence[str]] = None,
        max_items: int = 8,
        include_sensitive: bool = False,
    ) -> List[Dict[str, Any]]:
        """Return relevant context entities for a query and optional tool list.

        Section 10 requirement: context providers declare capability keys,
        sensitivity level, and freshness. This method selects context by
        capability and candidate tool metadata rather than app-name conditions.

        Returns a list of dicts with: type, label, handle (filtered), source,
        relevance_score, is_prompt_safe, is_cache_safe, is_fresh.
        """
        if not self._entities:
            return []
        now = self._now()
        query_tokens = set(_tokens(query))
        type_hint = infer_types_from_text(query) if query else None

        # Score every entity
        scored = []
        for entity in self._entities:
            base_score = self.score(entity, type_hint)
            # Query-term boost
            if query_tokens and query_tokens.intersection(_tokens(entity.label)):
                base_score += 2.0
            # Tool-candidate type boost
            if tool_candidates:
                for tc in tool_candidates:
                    tc_lower = (tc or "").lower()
                    if entity.type == "setting" and any(k in tc_lower for k in ("wifi", "bluetooth", "volume", "brightness")):
                        base_score += 1.5
                    elif entity.type in ("app", "window") and any(k in tc_lower for k in ("open", "close", "focus")):
                        base_score += 1.0
            scored.append((base_score, entity))

        scored.sort(key=lambda x: x[0], reverse=True)

        # Sensitivity classification
        _SENSITIVE_TYPES = {"clipboard"}
        _STALE_SECONDS = 300.0  # 5 minutes

        results = []
        for score_val, entity in scored[:max_items]:
            is_sensitive = entity.type in _SENSITIVE_TYPES
            if is_sensitive and not include_sensitive:
                continue
            is_fresh = (now - entity.last_seen) < _STALE_SECONDS if entity.last_seen > 0 else False
            # Filter handle for prompt safety: never include full clipboard text
            safe_handle = dict(entity.handle)
            if entity.type == "clipboard":
                safe_handle.pop("text", None)
                safe_handle["has_content"] = True
            results.append({
                "type": entity.type,
                "label": entity.label,
                "handle": safe_handle,
                "source": entity.source,
                "relevance_score": round(score_val, 2),
                "is_prompt_safe": entity.type not in _SENSITIVE_TYPES,
                "is_cache_safe": entity.type in ("setting", "app"),
                "is_log_safe": entity.type not in _SENSITIVE_TYPES,
                "is_fresh": is_fresh,
            })
        return results


# --------------------------------------------------------------------------- #
# Convenience builder (used by agent_loop) — fully fail-soft
# --------------------------------------------------------------------------- #


def build_registry(
    state: Optional[Dict[str, Any]] = None,
    conversation: Optional[Sequence[Tuple[Any, Any]]] = None,
    tool_results: Optional[Sequence[Dict[str, Any]]] = None,
    last_action: Optional[Tuple[str, str]] = None,
    alias_store: Optional[AliasStore] = None,
    now_fn: Callable[[], float] = time.time,
) -> ContextRegistry:
    reg = ContextRegistry(now_fn=now_fn, alias_store=alias_store)
    try:
        reg.build_from_state(state)
    except Exception as e:  # noqa: BLE001
        logger.debug("[CONTEXT] build_from_state failed: %s", e)
    try:
        reg.add_conversation(conversation)
    except Exception as e:  # noqa: BLE001
        logger.debug("[CONTEXT] add_conversation failed: %s", e)
    try:
        for i, tr in enumerate(tool_results or []):
            reg.add_tool_result(
                tr.get("tool", ""), tr.get("observation", ""),
                order=tr.get("order", i),
                handle=tr.get("handle"),
                type_=tr.get("type", "tool_result"),
            )
    except Exception as e:  # noqa: BLE001
        logger.debug("[CONTEXT] add_tool_result failed: %s", e)
    try:
        if last_action:
            reg.add_last_action_target(last_action[0], last_action[1])
    except Exception as e:  # noqa: BLE001
        logger.debug("[CONTEXT] add_last_action_target failed: %s", e)
    return reg


# --------------------------------------------------------------------------- #
# Active-registry handoff (agent_loop -> tools) + shared learned-alias store
# --------------------------------------------------------------------------- #

_active = threading.local()
_alias_store_singleton: Optional[AliasStore] = None
_alias_lock = threading.Lock()


def set_active_registry(reg: Optional[ContextRegistry]) -> None:
    """agent_loop sets the per-run registry so tools can resolve references
    ("isko/this") deterministically during a run. Thread-local => safe even if
    AgentLoop is shared across requests."""
    try:
        _active.registry = reg
    except Exception:  # noqa: BLE001
        pass


def get_active_registry() -> Optional[ContextRegistry]:
    """Current run's registry, or None. Tools use this to resolve references."""
    return getattr(_active, "registry", None)


def get_alias_store() -> AliasStore:
    """Process-wide learned-alias store (lazy singleton, fail-soft)."""
    global _alias_store_singleton
    if _alias_store_singleton is None:
        with _alias_lock:
            if _alias_store_singleton is None:
                _alias_store_singleton = AliasStore()
    return _alias_store_singleton


# Phrases we must NEVER persist as learned aliases: they are context-dependent
# and would poison the table ("this" today != "this" tomorrow).
_UNLEARNABLE = set(_PRONOUNS) | set(_ORDINALS.keys()) | {
    "the app", "the window", "the file", "the result", "last wala", "wala",
}


def learn_alias(phrase: str, entity: "ContextEntity",
                store: Optional[AliasStore] = None) -> bool:
    """Conservatively persist phrase -> entity. Returns True if stored.

    Reliability #1: NEVER learn pronouns/ordinals, blanks, very short phrases,
    or a phrase identical to the entity's own name. This keeps the alias table
    meaningful (e.g. "mera browser" -> chrome) and never poisons it with
    throwaway words like "this"/"isko"/"pehla".
    """
    try:
        p = (phrase or "").strip().lower()
        if not p or len(p) < 3:
            return False
        if p in _UNLEARNABLE or any(t in _UNLEARNABLE for t in _tokens(p)):
            return False
        if entity is None or not entity.label:
            return False
        if p == entity.label.strip().lower():
            return False
        st = store or get_alias_store()
        st.put(p, entity.label, entity.type, dict(entity.handle or {}))
        logger.info("[CONTEXT] learned alias %r -> %s(%s)", p, entity.type, entity.label)
        return True
    except Exception as e:  # noqa: BLE001
        logger.debug("[CONTEXT] learn_alias failed: %s", e)
        return False
