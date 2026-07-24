"""Shared value types for Phase 4 (Checker + Vision + Self-Learning).

Kept dependency-free so every Phase 4 module (and the unit tests) can import it
without pulling in heavy optional deps (openai / groq / pyautogui / psutil).
"""

from __future__ import annotations

from dataclasses import dataclass

# Verdicts produced by the Checker / Vision verifier.
PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"


@dataclass
class VerifyResult:
    """Outcome of verifying a single action.

    * verdict  : PASS / FAIL / UNKNOWN
    * reason   : short human-readable explanation ("window 'Notepad' is open")
    * evidence : the concrete signal we used (matched window title, setting
                 value, or a vision summary) -- stored for honest reporting.
    * source   : where the verdict came from -- "watcher" | "vision" | "tool"
                 | "none".
    """

    verdict: str = UNKNOWN
    reason: str = ""
    evidence: str = ""
    source: str = "none"

    def is_pass(self) -> bool:
        return self.verdict == PASS

    def is_fail(self) -> bool:
        return self.verdict == FAIL

    def to_payload(self) -> dict:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "evidence": self.evidence,
            "source": self.source,
        }
