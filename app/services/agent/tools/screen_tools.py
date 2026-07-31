"""Read what is actually on screen, as text.

`take_screenshot` already exists, but it produces an image that has to go to a
vision model -- a network round trip and a real cost per look. Most of the time
the question is simply "what does this say?": an error dialog, a form, a page.

Windows ships an OCR engine (`winsdk.windows.media.ocr`), and winsdk is already
a dependency here for the radio APIs, so this needs no new install and no
network. When OCR is unavailable the tools say so plainly and point at the
existing screenshot + vision path rather than pretending.
"""

from __future__ import annotations

import logging
import os
import time

from app.services.agent.tool_registry import tool

logger = logging.getLogger("J.A.R.V.I.S")

_MAX_TEXT = 12000


def _ocr_available() -> str:
    """Return "" when OCR can run, else a human-readable reason."""
    try:
        from winsdk.windows.media.ocr import OcrEngine
    except ImportError:
        return ("Windows OCR is unavailable ('winsdk' is not installed). "
                "Use take_screenshot instead.")
    try:
        if OcrEngine.try_create_from_user_profile_languages() is None:
            return ("Windows OCR has no language pack for your profile. "
                    "Use take_screenshot instead.")
    except Exception as exc:  # noqa: BLE001
        return f"Windows OCR could not start ({exc}). Use take_screenshot instead."
    return ""


def _grab(region=None):
    """Screenshot the whole screen or a region, as a PIL image."""
    import pyautogui
    return pyautogui.screenshot(region=region) if region else pyautogui.screenshot()


def _ocr_image(image) -> str:
    """Run Windows OCR over a PIL image and return its text, line by line."""
    import asyncio
    import io

    from winsdk.windows.graphics.imaging import BitmapDecoder
    from winsdk.windows.media.ocr import OcrEngine
    from winsdk.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    payload = buffer.getvalue()

    async def _run() -> str:
        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream.get_output_stream_at(0))
        writer.write_bytes(payload)
        await writer.store_async()
        await writer.flush_async()
        writer.detach_stream()
        stream.seek(0)

        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            return ""
        result = await engine.recognize_async(bitmap)
        return "\n".join(line.text for line in result.lines)

    # A fresh loop: this runs on a worker thread, and the WinRT awaitables must
    # not be attached to a loop owned by someone else.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.close()


@tool(
    name="read_screen",
    description=(
        "Read the text currently visible on screen and return it. Use this to "
        "answer 'what does this say', 'what is this error', 'read this page' -- "
        "it is much faster and cheaper than taking a screenshot for the vision "
        "model. Text only: for colours, layout or images use take_screenshot."
    ),
    params={},
    category="desktop",
    verification={"family": "query", "cacheable": False},
)
def read_screen() -> str:
    problem = _ocr_available()
    if problem:
        return f"ERROR: {problem}"
    try:
        text = _ocr_image(_grab())
    except Exception as e:  # noqa: BLE001
        return f"ERROR: could not read the screen: {e}"
    if not text.strip():
        return "No readable text found on screen."
    truncated = len(text) > _MAX_TEXT
    body = text[:_MAX_TEXT] + ("\n... [truncated]" if truncated else "")
    return f"Text on screen:\n{body}"


@tool(
    name="read_screen_region",
    description=(
        "Read the text inside one rectangle of the screen. Use when the screen "
        "is busy and only part of it matters -- a dialog, a panel, one corner."
    ),
    params={
        "left": {"type": "int", "description": "Left edge in pixels."},
        "top": {"type": "int", "description": "Top edge in pixels."},
        "width": {"type": "int", "description": "Width in pixels."},
        "height": {"type": "int", "description": "Height in pixels."},
    },
    category="desktop",
    verification={"family": "query", "cacheable": False},
)
def read_screen_region(left: int, top: int, width: int, height: int) -> str:
    problem = _ocr_available()
    if problem:
        return f"ERROR: {problem}"
    try:
        width, height = int(width), int(height)
        if width <= 0 or height <= 0:
            return "ERROR: width and height must be positive."
        text = _ocr_image(_grab(region=(int(left), int(top), width, height)))
    except Exception as e:  # noqa: BLE001
        return f"ERROR: could not read that region: {e}"
    if not text.strip():
        return "No readable text in that region."
    return f"Text in region ({left},{top} {width}x{height}):\n{text[:_MAX_TEXT]}"


@tool(
    name="screen_region_capture",
    description=(
        "Save a screenshot of one rectangle of the screen to a PNG file and "
        "return its path. Use when the user wants a crop, or when an image (not "
        "text) needs to go to the vision model."
    ),
    params={
        "left": {"type": "int", "description": "Left edge in pixels."},
        "top": {"type": "int", "description": "Top edge in pixels."},
        "width": {"type": "int", "description": "Width in pixels."},
        "height": {"type": "int", "description": "Height in pixels."},
        "path": {"type": "string", "required": False,
                 "description": "Where to save. Defaults to the captures folder."},
    },
    category="desktop",
    verification={"family": "file"},
)
def screen_region_capture(left: int, top: int, width: int, height: int,
                          path: str = "") -> str:
    try:
        width, height = int(width), int(height)
        if width <= 0 or height <= 0:
            return "ERROR: width and height must be positive."
        if path:
            from app.services.agent.tools.file_tools import _guard_path, _resolve
            target = _resolve(path)
            blocked = _guard_path(target, "write to")
            if blocked:
                return blocked
        else:
            from config import CAMERA_CAPTURES_DIR
            folder = str(CAMERA_CAPTURES_DIR)
            os.makedirs(folder, exist_ok=True)
            target = os.path.join(folder, f"region_{int(time.time())}.png")

        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        _grab(region=(int(left), int(top), width, height)).save(target)
        logger.info("[SCREEN] Saved region capture to %s", target)
        return f"Saved a {width}x{height} capture to {target}."
    except Exception as e:  # noqa: BLE001
        return f"ERROR: could not capture that region: {e}"
