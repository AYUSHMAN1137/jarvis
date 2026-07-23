"""Skill-maker (Phase 4, Chunk 4).

Listens for `verified` events on the bus. When an action is verified PASS, it
logs the success into the SkillStore. The store applies the "N baar repeat"
fluke-guard, so only genuinely repeated, verified successes ever crystallize
into reusable skills.

v1 scope: each verified single tool-call becomes a one-step skill keyed by the
user's command. Multi-step skills are a Phase 5 concern (the store schema and
this subscriber already accept a list of steps, so no rewrite is needed later).
"""

from __future__ import annotations

import logging

from app.services.agent.phase4 import models

logger = logging.getLogger("J.A.R.V.I.S")


class SkillMaker:
    def __init__(self, store, bus=None) -> None:
        self.store = store
        self.bus = bus
        if bus is not None:
            bus.subscribe("verified", self._on_verified)

    def _on_verified(self, payload: dict) -> None:
        try:
            if (payload or {}).get("verdict") != models.PASS:
                return
            # Prefer the user's natural command as the trigger; fall back to the
            # tool name so a skill is still keyed by *something* stable.
            trigger = (payload.get("user_message") or "").strip() or payload.get("tool", "")
            if not trigger:
                return
            steps = payload.get("steps") or [
                {"tool": payload.get("tool"), "args": payload.get("args", {})}
            ]
            res = self.store.record_verified_success(trigger, steps)
            if res.get("crystallized"):
                logger.info(
                    "[SKILL-MAKER] new skill saved for '%s' (repeats=%d).",
                    trigger, res.get("repeats", 0),
                )
        except Exception as e:  # noqa: BLE001 - subscribers must never raise
            logger.debug("[SKILL-MAKER] _on_verified failed: %s", e)
