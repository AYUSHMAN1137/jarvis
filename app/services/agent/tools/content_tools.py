"""
Content generation tools: image generation (Pollinations) and written content.

These are server-side generations whose results are shown in the browser, so
they push frontend actions via the action_sink:
  - generate_image  -> pushes an image URL
  - write_content   -> pushes the written text
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from app.services.agent.tool_registry import tool
from app.services.agent import action_sink
from app.services.agent.deps import deps

logger = logging.getLogger("J.A.R.V.I.S")


@tool(
    name="generate_image",
    description=(
        "Generate / draw / create an image from a text description. Keep the "
        "full visual description. The image is shown to the user in the browser."
    ),
    params={
        "prompt": {
            "type": "string",
            "description": "Detailed visual description of the image to generate.",
        }
    },
    category="content",
)
def generate_image(prompt: str) -> str:
    p = (prompt or "").strip()
    if len(p) < 3:
        return "ERROR: the image description is too short."
    encoded = quote(p[:4000], safe="")
    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?model=flux&width=1024&height=1024&nologo=true&private=true&enhance=true&safe=false"
    )
    action_sink.add_image(url)
    logger.info("[CONTENT] generate_image -> %.60s", p)
    return f"Generated an image of: {p}."


@tool(
    name="write_content",
    description=(
        "Write text content such as an essay, poem, letter, email, story, or "
        "code. The written content is shown to the user."
    ),
    params={
        "prompt": {
            "type": "string",
            "description": "What to write, e.g. 'a poem about the stars'.",
        }
    },
    category="content",
)
def write_content(prompt: str) -> str:
    p = (prompt or "").strip()
    if not p:
        return "ERROR: nothing to write."
    if not deps.groq_service:
        return "ERROR: content generation service is unavailable."
    question = (
        "Write the following. Be thorough and well-structured. "
        "Return only the requested content, no preamble.\n\n" + p
    )
    try:
        out = deps.groq_service.get_response(question=question, chat_history=[], key_start_index=0)
        if not out or len(out.strip()) < 10:
            return "ERROR: content generation returned nothing."
        action_sink.add_content(out)
        logger.info("[CONTENT] write_content -> %d chars", len(out))
        return f"Wrote the content ({len(out)} characters). It is now shown to the user."
    except Exception as e:  # noqa: BLE001
        return f"ERROR: writing failed: {e}"
