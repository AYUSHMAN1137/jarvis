"""Vision verifier (Phase 4, Chunk 3).

Used by the Checker ONLY as a fallback, when the watcher cannot decide whether
an action succeeded (verdict == UNKNOWN). It captures the screen and asks a
multimodal model for a PASS/FAIL judgement plus a short reason.

Provider strategy (locked decision = Gemini multimodal):
  1. Gemini (reuses the 10-key pool via llm_providers, OpenAI-compatible).
  2. Fallback to the existing Groq VisionService if Gemini is unavailable.
Everything is lazily imported and fail-soft -- if vision can't run, we return
UNKNOWN rather than guessing (no fake verdicts).
"""

from __future__ import annotations

import logging

import config as _cfg
from app.services.agent.phase4 import models
from app.services.agent.phase4.models import VerifyResult

logger = logging.getLogger("J.A.R.V.I.S")

_VISION_MODEL = getattr(
    _cfg, "GEMINI_VISION_MODEL", getattr(_cfg, "GEMINI_MODEL", "gemini-2.0-flash")
)


class VisionVerifier:
    """Screen-based PASS/FAIL judge. Fail-soft -> UNKNOWN when unavailable."""

    def available(self) -> bool:
        try:
            from app.services import llm_providers
            if llm_providers.gemini_enabled():
                return True
        except Exception:  # noqa: BLE001
            pass
        # Groq vision fallback is considered available if the service imports.
        try:
            from app.services.vision_service import VisionService  # noqa: F401
            return True
        except Exception:  # noqa: BLE001
            return False

    def verify(self, question: str) -> VerifyResult:
        """Capture the screen and judge PASS/FAIL for the given question."""
        img_b64 = self._capture()
        if not img_b64:
            return VerifyResult(models.UNKNOWN, reason="no screenshot", source="vision")
        text = self._ask(question, img_b64)
        if not text:
            return VerifyResult(models.UNKNOWN, reason="vision unavailable", source="vision")
        up = text.strip().upper()
        has_pass, has_fail = ("PASS" in up), ("FAIL" in up)
        if up.startswith("PASS") or (has_pass and not has_fail):
            verdict = models.PASS
        elif up.startswith("FAIL") or (has_fail and not has_pass):
            verdict = models.FAIL
        else:
            verdict = models.UNKNOWN
        return VerifyResult(
            verdict, reason=text.strip()[:200], evidence="screen", source="vision"
        )

    # -- internals ------------------------------------------------------- #
    def _capture(self) -> str:
        try:
            import base64
            import io
            import pyautogui
            img = pyautogui.screenshot()
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception as e:  # noqa: BLE001
            logger.debug("[VISION] screen capture failed: %s", e)
            return ""

    def _ask(self, question: str, img_b64: str) -> str:
        prompt = (
            "You verify whether a computer action succeeded by looking at this "
            "screenshot. Answer with exactly PASS or FAIL on the first line, then "
            "one short sentence of reason. " + (question or "")
        )
        # 1) Gemini (preferred, multimodal, OpenAI-compatible)
        try:
            from app.services import llm_providers
            if llm_providers.gemini_enabled():
                client = llm_providers.make_raw_client(llm_providers.next_key())
                data_url = "data:image/png;base64," + img_b64
                resp = client.chat.completions.create(
                    model=_VISION_MODEL,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    }],
                    max_tokens=200,
                    temperature=0,
                )
                return (resp.choices[0].message.content or "").strip()
        except Exception as e:  # noqa: BLE001
            logger.debug("[VISION] Gemini verify failed: %s", e)
        # 2) Groq VisionService fallback
        try:
            from app.services.vision_service import VisionService
            return (VisionService().describe_image(img_b64, prompt) or "").strip()
        except Exception as e:  # noqa: BLE001
            logger.debug("[VISION] Groq verify fallback failed: %s", e)
            return ""
