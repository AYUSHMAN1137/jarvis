"""Section 8: Cache promotion policy interface.

Instead of a single hardcoded success count, promotion decisions consider risk,
evidence strength, determinism, verifier reliability, and context dependencies.

The DefaultPromotionPolicy implements the suggested rules from the implementation
plan (Section 8.3). Policies are configuration-driven through config.py values.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import config as _cfg

logger = logging.getLogger("J.A.R.V.I.S")

# Risk categories from config (Section 17)
_PROMOTION_STRICT_RISK = {"dangerous", "irreversible", "destructive"}


class PromotionDecision:
    """Result of a promotion policy evaluation."""
    __slots__ = ("promote", "reason", "evidence_class")

    def __init__(self, promote: bool, reason: str, evidence_class: str = ""):
        self.promote = promote
        self.reason = reason
        self.evidence_class = evidence_class

    def __repr__(self) -> str:
        return f"PromotionDecision(promote={self.promote}, reason={self.reason!r})"


class PromotionPolicy:
    """Base policy interface: override evaluate() for custom rules."""

    def evaluate(
        self,
        risk: str,
        determinism: str,
        evidence_strength: str,
        verifier_reliability: str,
        context_dependencies: List[str],
        complete_execution: bool,
        verdict: str,
        previous_observations: int = 0,
    ) -> PromotionDecision:
        raise NotImplementedError


class DefaultPromotionPolicy(PromotionPolicy):
    """Implementation plan Section 8.3: category-driven promotion rules.

    - deterministic + low risk + strong state verifier + complete PASS: promote after one execution.
    - weak/transport-only evidence: require repeated consistent evidence or do not auto-promote.
    - context-dependent: store only with validated context dependencies.
    - dangerous/irreversible: never silently replay.
    - UNKNOWN: never promote.
    """

    def evaluate(
        self,
        risk: str = "safe",
        determinism: str = "deterministic",
        evidence_strength: str = "state",
        verifier_reliability: str = "high",
        context_dependencies: List[str] = None,
        complete_execution: bool = True,
        verdict: str = "UNKNOWN",
        previous_observations: int = 0,
    ) -> PromotionDecision:
        # Never promote non-PASS
        if verdict != "PASS":
            return PromotionDecision(False, f"verdict is {verdict}, not PASS")

        # Never promote incomplete executions
        if not complete_execution:
            return PromotionDecision(False, "execution is incomplete")

        # Never silently replay dangerous/irreversible actions
        if risk in _PROMOTION_STRICT_RISK:
            return PromotionDecision(False, f"risk category '{risk}' blocks promotion",
                                     evidence_class="blocked")

        # Context-dependent: require all dependencies captured
        if context_dependencies:
            return PromotionDecision(False, "context-dependent action requires validated dependencies",
                                     evidence_class="context")

        # Strong state verifier + deterministic + low risk: promote immediately
        if (determinism == "deterministic" and risk == "safe"
                and evidence_strength in ("state", "strong")
                and verifier_reliability in ("high", "reliable")):
            return PromotionDecision(True, "deterministic + safe + strong verifier",
                                     evidence_class="strong")

        # Weak/transport-only evidence: require repeated observations
        if evidence_strength in ("transport", "weak", "none"):
            min_required = int(getattr(_cfg, "CACHE_WEAK_EVIDENCE_MIN_OBS", 3))
            if previous_observations >= min_required:
                return PromotionDecision(True,
                                         f"weak evidence but {previous_observations} consistent observations",
                                         evidence_class="weak_repeated")
            return PromotionDecision(False,
                                     f"weak evidence, only {previous_observations}/{min_required} observations",
                                     evidence_class="weak")

        # Default: promote with state evidence
        return PromotionDecision(True, "verified with adequate evidence",
                                 evidence_class="standard")


# Singleton
_policy: Optional[DefaultPromotionPolicy] = None


def get_promotion_policy() -> DefaultPromotionPolicy:
    global _policy
    if _policy is None:
        _policy = DefaultPromotionPolicy()
    return _policy
