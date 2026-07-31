"""Command signatures -- decide when two phrasings mean the same request.

WHY
---
The cache used to key on the exact normalised string, so it only ever hit when
the user repeated a command word for word. In a real 26-turn session it scored
**zero** hits: "Check for update." and "So click on Check Update." were stored
and looked up as different commands, and the same 60-second failing path ran
twice.

Full semantic matching (embeddings) is tempting but dangerous here: a cached
entry *executes something*, so a false positive performs the wrong action. This
module takes the conservative route instead -- an explainable signature, with the
parts that change meaning kept strictly separate:

  verbs    canonical action class (OPEN vs CLOSE must never be confused)
  state    on / off / none (turning something on is not turning it off)
  numbers  exact (volume 30 is not volume 70)
  core     the remaining subject words (what is being acted on)

Two commands match only when the verbs are compatible, the state is identical,
the numbers are identical, and the subjects overlap strongly. Everything here is
pure, so it is fully unit-testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import FrozenSet, Optional, Tuple

# Politeness, auxiliaries and filler. Dropping these cannot change the meaning.
_FILLER = frozenset({
    "please", "pls", "kindly", "just", "now", "so", "then", "also", "and", "the",
    "a", "an", "my", "me", "i", "you", "your", "it", "its", "for", "to", "of",
    "in", "into", "at", "with", "can", "could", "would", "will", "shall", "do",
    "does", "did", "kar", "karo", "kardo", "kro", "kar_do", "dijiye", "dena",
    "de", "do_na", "zara", "abhi", "bhi", "hai", "ho", "jao", "ja", "ke", "ki",
    "ka", "ko", "se", "par", "pe", "mera", "meri", "mere", "wala", "wali",
    "computer", "pc", "laptop", "system", "windows",
})

# Canonical action classes. Two commands with incompatible verb sets never match:
# this is what stops "open notepad" being served from "close notepad".
_VERBS = {
    "open": "OPEN", "launch": "OPEN", "start": "OPEN", "run": "OPEN",
    "kholo": "OPEN", "khol": "OPEN", "chalao": "OPEN",
    "close": "CLOSE", "quit": "CLOSE", "exit": "CLOSE", "kill": "CLOSE",
    "terminate": "CLOSE", "band": "CLOSE", "bandh": "CLOSE",
    "click": "CLICK", "press": "CLICK", "tap": "CLICK", "dabao": "CLICK",
    "check": "CHECK", "verify": "CHECK", "look": "CHECK", "show": "CHECK",
    "tell": "CHECK", "list": "CHECK", "batao": "CHECK", "dekho": "CHECK",
    "set": "SET", "change": "SET", "make": "SET", "adjust": "SET",
    "badlo": "SET", "badal": "SET",
    "play": "PLAY", "chala": "PLAY", "chalu": "PLAY", "bajao": "PLAY",
    "pause": "PAUSE", "stop": "PAUSE", "roko": "PAUSE",
    "save": "SAVE", "store": "SAVE", "download": "SAVE",
    "write": "WRITE", "type": "WRITE", "likho": "WRITE",
    "delete": "DELETE", "remove": "DELETE", "hatao": "DELETE",
    "send": "SEND", "search": "SEARCH", "find": "SEARCH", "dhundo": "SEARCH",
    "install": "INSTALL", "update": "UPDATE", "connect": "CONNECT",
    "mute": "MUTE", "unmute": "UNMUTE", "increase": "UP", "raise": "UP",
    "decrease": "DOWN", "lower": "DOWN", "reduce": "DOWN",
    "toggle": "TOGGLE", "switch": "", "turn": "",
}

# Words that express the desired state. These are extracted, not dropped: a
# missed "off" would let "turn it on" be served from "turn it off".
# "off" is effectively unambiguous in commands. "on" is NOT -- it is usually a
# preposition ("click on X", "go on"), so a bare "on" must not be read as a
# state or "click on check update" stops matching "check update".
_ON_WORDS = frozenset({"enable", "enabled", "activate", "chalu"})
_OFF_WORDS = frozenset({"off", "disable", "disabled", "deactivate", "band", "bandh"})
# Explicit "switch it on" shapes, where "on" really is the desired state.
_ON_PATTERN = re.compile(
    r"\b(?:turn|switch|toggle|put|kar|karo|kardo)\b[^.]{0,24}\bon\b|\bon\s+(?:kar|karo|kardo)\b|\bon\s*$"
)
_OFF_PATTERN = re.compile(
    r"\b(?:turn|switch|toggle|put|kar|karo|kardo)\b[^.]{0,24}\boff\b|\boff\s+(?:kar|karo|kardo)\b|\boff\s*$"
)


@dataclass(frozen=True)
class Signature:
    """Meaning-bearing fingerprint of a command."""
    core: FrozenSet[str]
    verbs: FrozenSet[str]
    state: str            # "on" | "off" | ""
    numbers: Tuple[float, ...]

    def is_empty(self) -> bool:
        return not self.core and not self.verbs and not self.state and not self.numbers


def _words(text: str):
    return [w for w in re.split(r"[^a-z0-9\u0900-\u097F]+", str(text or "").lower()) if w]


def command_signature(text: str) -> Signature:
    """Build the signature of a command. Pure and deterministic."""
    core, verbs = set(), set()
    numbers = []
    state = ""

    for word in _words(text):
        if re.fullmatch(r"\d+(\.\d+)?", word):
            try:
                numbers.append(float(word))
            except ValueError:
                pass
            continue
        if word in _ON_WORDS:
            state = state or "on"
            continue
        if word in _OFF_WORDS:
            state = "off"
            continue
        if word == "on":
            # Decided below from sentence shape, never from the bare word.
            continue
        if word in _VERBS:
            canonical = _VERBS[word]
            if canonical:
                verbs.add(canonical)
            # A verb that maps to "" (turn/switch) carries no meaning by itself;
            # the state word next to it does.
            continue
        if word in _FILLER or len(word) < 2:
            continue
        core.add(word)

    # Decide an "on" state from sentence shape, not from the bare preposition.
    if not state:
        lowered = " ".join(_words(text))
        if _OFF_PATTERN.search(lowered):
            state = "off"
        elif _ON_PATTERN.search(lowered):
            state = "on"

    return Signature(frozenset(core), frozenset(verbs), state, tuple(sorted(numbers)))


def _overlap(a: FrozenSet[str], b: FrozenSet[str]) -> float:
    """Symmetric containment: how much of the smaller set is inside the larger."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / float(min(len(a), len(b)))


def verbs_compatible(a: FrozenSet[str], b: FrozenSet[str]) -> bool:
    """Compatible means one verb set is contained in the other.

    "check update" and "click check update" are compatible ({CHECK} inside
    {CLICK, CHECK}). "open notepad" and "close notepad" are not, and must never
    be treated as the same request.
    """
    if not a or not b:
        return True
    return a.issubset(b) or b.issubset(a)


def signatures_match(a: Signature, b: Signature, min_core: float = 0.7) -> bool:
    """Do these two commands ask for the same thing? Conservative by design."""
    if a.is_empty() or b.is_empty():
        return False
    if a.state != b.state:
        return False
    if a.numbers != b.numbers:
        return False
    if not verbs_compatible(a.verbs, b.verbs):
        return False
    if not a.core and not b.core:
        # No subject words at all ("check for update"): the compatible verbs
        # checked above are the whole meaning, so require only that both have one.
        return bool(a.verbs) and bool(b.verbs)
    return _overlap(a.core, b.core) >= float(min_core)


def similarity(a: Signature, b: Signature) -> float:
    """Explainable 0..1 score, for logs and for ranking several candidates."""
    if a.state != b.state or a.numbers != b.numbers:
        return 0.0
    if not verbs_compatible(a.verbs, b.verbs):
        return 0.0
    core = _overlap(a.core, b.core)
    verb = 1.0 if (a.verbs and a.verbs == b.verbs) else 0.85
    return round(core * verb, 3)


def match_command(query: str, candidates, min_core: float = 0.7) -> Optional[Tuple[str, float]]:
    """Best matching candidate command for `query`, or None.

    `candidates` is any iterable of stored command strings.
    """
    query_sig = command_signature(query)
    if query_sig.is_empty():
        return None
    best, best_score = None, 0.0
    for candidate in candidates:
        candidate_sig = command_signature(candidate)
        if not signatures_match(query_sig, candidate_sig, min_core=min_core):
            continue
        score = similarity(query_sig, candidate_sig)
        if score > best_score:
            best, best_score = candidate, score
    if best is None:
        return None
    return best, best_score
