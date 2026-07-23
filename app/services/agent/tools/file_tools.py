"""
Local file-system tools (run on the server machine).

Useful for desktop workflows like "save this to a file", "what's in this
folder", "open this file". Delete is marked dangerous so it needs confirmation.
"""

from __future__ import annotations

import logging
import os
import subprocess

from app.services.agent.tool_registry import tool

logger = logging.getLogger("J.A.R.V.I.S")

_MAX_READ = 20000  # chars

# Common user folders that a person refers to by bare name ("desktop pe ...").
# Mapping a leading 'desktop'/'downloads'/etc. to the real home folder is NOT a
# hardcoded app behaviour -- it's just resolving the well-known Windows user
# directories so natural commands work.
_USER_DIRS = {
    "desktop", "downloads", "documents", "pictures", "music", "videos", "desktop",
}


def _resolve(path: str) -> str:
    """Resolve a user-given path: expand ~ and env vars, and treat a leading
    well-known folder name (desktop/downloads/...) as relative to the home dir.
    """
    p = (path or "").strip().strip('"').strip("'")
    if not p:
        return ""
    p = os.path.expandvars(os.path.expanduser(p))
    if not os.path.isabs(p):
        norm = p.replace("\\", "/")
        first = norm.split("/")[0].lower()
        if first in _USER_DIRS:
            home = os.path.expanduser("~")
            return os.path.normpath(os.path.join(home, *norm.split("/")))
    return p


@tool(
    name="list_directory",
    description="List the files and folders inside a directory on the computer.",
    params={"path": {"type": "string", "description": "Directory path to list."}},
    category="desktop",
)
def list_directory(path: str) -> str:
    p = _resolve(path)
    if not p:
        return "ERROR: no path given."
    if not os.path.isdir(p):
        return f"ERROR: '{p}' is not a directory."
    try:
        entries = sorted(os.listdir(p))
        if not entries:
            return f"'{p}' is empty."
        lines = []
        for name in entries[:200]:
            full = os.path.join(p, name)
            kind = "DIR " if os.path.isdir(full) else "FILE"
            lines.append(f"[{kind}] {name}")
        more = "" if len(entries) <= 200 else f"\n... and {len(entries) - 200} more"
        return f"Contents of {p}:\n" + "\n".join(lines) + more
    except Exception as e:  # noqa: BLE001
        return f"ERROR: could not list '{p}': {e}"


@tool(
    name="read_file",
    description="Read and return the text content of a file on the computer.",
    params={"path": {"type": "string", "description": "File path to read."}},
    category="desktop",
)
def read_file(path: str) -> str:
    p = _resolve(path)
    if not os.path.isfile(p):
        return f"ERROR: '{p}' is not a file."
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            data = f.read(_MAX_READ + 1)
        truncated = len(data) > _MAX_READ
        return data[:_MAX_READ] + ("\n... [truncated]" if truncated else "")
    except Exception as e:  # noqa: BLE001
        return f"ERROR: could not read '{p}': {e}"


@tool(
    name="write_file",
    description=(
        "Write text content to a file on the computer (creates or overwrites). "
        "Use for saving notes, code, or generated text to disk."
    ),
    params={
        "path": {"type": "string", "description": "File path to write to."},
        "content": {"type": "string", "description": "Text content to write."},
    },
    category="desktop",
)
def write_file(path: str, content: str) -> str:
    p = _resolve(path)
    if not p:
        return "ERROR: no path given."
    try:
        parent = os.path.dirname(p)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content or "")
        logger.info("[FILE] Wrote %d chars to %s", len(content or ""), p)
        return f"Saved {len(content or '')} characters to {p}."
    except Exception as e:  # noqa: BLE001
        return f"ERROR: could not write '{p}': {e}"


@tool(
    name="open_file",
    description="Open a file or folder with its default application on Windows.",
    params={"path": {"type": "string", "description": "File or folder path to open."}},
    category="desktop",
)
def open_file(path: str) -> str:
    p = _resolve(path)
    if not os.path.exists(p):
        return f"ERROR: '{p}' does not exist."
    try:
        os.startfile(p)  # type: ignore[attr-defined]  # Windows-only
        return f"Opened {p}."
    except Exception as e:  # noqa: BLE001
        return f"ERROR: could not open '{p}': {e}"


@tool(
    name="delete_file",
    description=(
        "Delete a file from the computer. Destructive and permanent. "
        "Only call after the user has confirmed."
    ),
    params={"path": {"type": "string", "description": "File path to delete."}},
    dangerous=True,
    category="desktop",
)
def delete_file(path: str) -> str:
    p = _resolve(path)
    if not os.path.isfile(p):
        return f"ERROR: '{p}' is not a file."
    try:
        os.remove(p)
        logger.info("[FILE] Deleted %s", p)
        return f"Deleted {p}."
    except Exception as e:  # noqa: BLE001
        return f"ERROR: could not delete '{p}': {e}"


@tool(
    name="create_folder",
    description=(
        "Create a new folder / directory on the computer. Handles common roots "
        "like Desktop, Downloads, Documents by name. Example: path "
        "'desktop/ayushman' makes a folder named 'ayushman' on the Desktop."
    ),
    params={"path": {"type": "string", "description": "Folder path to create, e.g. 'desktop/ayushman'."}},
    category="desktop",
)
def create_folder(path: str) -> str:
    p = _resolve(path)
    if not p:
        return "ERROR: no folder path given."
    try:
        if os.path.isdir(p):
            return f"Folder already exists: {p}."
        os.makedirs(p, exist_ok=True)
        logger.info("[FILE] Created folder %s", p)
        return f"Created folder {p}."
    except Exception as e:  # noqa: BLE001
        return f"ERROR: could not create folder '{p}': {e}"


@tool(
    name="move_to_trash",
    description=(
        "Move a file or folder to the Recycle Bin (recoverable, safer than "
        "delete). Use for 'recycle bin me daalo' / 'trash me bhejo'."
    ),
    params={"path": {"type": "string", "description": "File or folder path to move to the Recycle Bin."}},
    category="desktop",
)
def move_to_trash(path: str) -> str:
    p = _resolve(path)
    if not p:
        return "ERROR: no path given."
    if not os.path.exists(p):
        return f"ERROR: '{p}' does not exist."
    try:
        from send2trash import send2trash
        send2trash(p)
        logger.info("[FILE] Sent to Recycle Bin: %s", p)
        return f"Moved {p} to the Recycle Bin."
    except ImportError:
        return (
            "ERROR: Recycle Bin support needs the 'send2trash' package "
            "(pip install send2trash)."
        )
    except Exception as e:  # noqa: BLE001
        return f"ERROR: could not move '{p}' to the Recycle Bin: {e}"


@tool(
    name="move_path",
    description=(
        "Move or rename a file or folder from a source path to a destination "
        "path. Handles Desktop/Downloads/Documents by name."
    ),
    params={
        "source": {"type": "string", "description": "Existing file/folder path."},
        "destination": {"type": "string", "description": "New path or folder to move into."},
    },
    category="desktop",
)
def move_path(source: str, destination: str) -> str:
    src = _resolve(source)
    dst = _resolve(destination)
    if not src or not dst:
        return "ERROR: both source and destination are required."
    if not os.path.exists(src):
        return f"ERROR: '{src}' does not exist."
    try:
        import shutil
        # If destination is an existing directory, move INTO it.
        if os.path.isdir(dst):
            dst = os.path.join(dst, os.path.basename(src.rstrip("/\\")))
        parent = os.path.dirname(dst)
        if parent:
            os.makedirs(parent, exist_ok=True)
        shutil.move(src, dst)
        logger.info("[FILE] Moved %s -> %s", src, dst)
        return f"Moved to {dst}."
    except Exception as e:  # noqa: BLE001
        return f"ERROR: could not move '{src}': {e}"
