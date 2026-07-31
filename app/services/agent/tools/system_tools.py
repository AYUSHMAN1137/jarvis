"""
System control tools (Windows): volume, brightness, media keys, power.

Power actions (shutdown / restart / sleep / lock) are marked dangerous=True so
the agent loop asks the user for confirmation before running them.
"""

from __future__ import annotations

import logging
import subprocess

from app.services.agent.tool_registry import tool

logger = logging.getLogger("J.A.R.V.I.S")


def _set_volume_pycaw(level: int) -> bool:
    """Set master volume (0-100) using pycaw. Returns True on success."""
    try:
        from app.services.agent.tools._audio import set_master_volume

        set_master_volume(level)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("[SYSTEM] pycaw volume failed: %s", e)
        return False


@tool(
    name="set_volume",
    description="Set the system master volume to a percentage from 0 to 100.",
    params={"level": {"type": "int", "description": "Volume percent, 0-100."}},
    category="system",
    verification={"family": "toggle", "capability": "system.volume"},
)
def set_volume(level: int) -> str:
    try:
        level = int(level)
    except (TypeError, ValueError):
        return "ERROR: level must be a number 0-100."
    level = max(0, min(100, level))
    if _set_volume_pycaw(level):
        logger.info("[SYSTEM] Volume set to %d%%", level)
        return f"Volume set to {level}%."
    return "ERROR: could not change the volume on this system."


@tool(
    name="mute_volume",
    description="Mute or unmute the system audio.",
    params={
        "mute": {
            "type": "boolean",
            "description": "True to mute, False to unmute.",
        }
    },
    category="system",
    verification={"family": "toggle", "capability": "system.mute"},
)
def mute_volume(mute: bool = True) -> str:
    try:
        from app.services.agent.tools._audio import set_mute

        set_mute(mute)
        return "Muted." if mute else "Unmuted."
    except Exception as e:  # noqa: BLE001
        return f"ERROR: could not change mute state: {e}"


@tool(
    name="set_brightness",
    description="Set the screen brightness to a percentage from 0 to 100.",
    params={"level": {"type": "int", "description": "Brightness percent, 0-100."}},
    category="system",
    verification={"family": "toggle", "capability": "system.brightness"},
)
def set_brightness(level: int) -> str:
    try:
        level = int(level)
    except (TypeError, ValueError):
        return "ERROR: level must be a number 0-100."
    level = max(0, min(100, level))
    try:
        import screen_brightness_control as sbc
        sbc.set_brightness(level)
        logger.info("[SYSTEM] Brightness set to %d%%", level)
        return f"Brightness set to {level}%."
    except Exception as e:  # noqa: BLE001
        return f"ERROR: could not change brightness: {e}"


@tool(
    name="media_control",
    description=(
        "Control media playback using virtual media keys. "
        "Action is one of: playpause, next, previous, stop, volumeup, volumedown, mute."
    ),
    params={
        "action": {
            "type": "string",
            "description": "Media action to perform.",
            "enum": ["playpause", "next", "previous", "stop",
                     "volumeup", "volumedown", "mute"],
        }
    },
    category="system",
    verification={"family": "none"},
)
def media_control(action: str) -> str:
    key_map = {
        "playpause": "playpause",
        "next": "nexttrack",
        "previous": "prevtrack",
        "stop": "stop",
        "volumeup": "volumeup",
        "volumedown": "volumedown",
        "mute": "volumemute",
    }
    key = key_map.get((action or "").strip().lower())
    if not key:
        return f"ERROR: unknown media action '{action}'."
    try:
        import pyautogui
        pyautogui.press(key)
        logger.info("[SYSTEM] Media control: %s", action)
        return f"Media: {action}."
    except Exception as e:  # noqa: BLE001
        return f"ERROR: media control failed: {e}"


@tool(
    name="lock_screen",
    description="Lock the Windows session (requires the user to sign in again).",
    params={},
    dangerous=False,
    category="system",
    verification={"family": "none"},
)
def lock_screen() -> str:
    try:
        subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"], timeout=5)
        return "Locked the screen."
    except Exception as e:  # noqa: BLE001
        return f"ERROR: could not lock the screen: {e}"


@tool(
    name="shutdown_computer",
    description=(
        "Shut down the computer. This is a destructive action and closes "
        "everything. Only call after the user has confirmed."
    ),
    params={
        "delay_seconds": {
            "type": "int",
            "description": "Seconds to wait before shutdown.",
            "required": False,
        }
    },
    dangerous=True,
    category="system",
    verification={"family": "none"},
)
def shutdown_computer(delay_seconds: int = 5) -> str:
    try:
        delay = max(0, int(delay_seconds))
    except (TypeError, ValueError):
        delay = 5
    try:
        subprocess.Popen(["shutdown", "/s", "/t", str(delay)])
        logger.info("[SYSTEM] Shutdown scheduled in %ds", delay)
        return f"Shutting down in {delay} seconds. Run cancel_power_action to abort."
    except Exception as e:  # noqa: BLE001
        return f"ERROR: could not shut down: {e}"


@tool(
    name="restart_computer",
    description=(
        "Restart / reboot the computer. Destructive: closes everything. "
        "Only call after the user has confirmed."
    ),
    params={
        "delay_seconds": {
            "type": "int",
            "description": "Seconds to wait before restart.",
            "required": False,
        }
    },
    dangerous=True,
    category="system",
    verification={"family": "none"},
)
def restart_computer(delay_seconds: int = 5) -> str:
    try:
        delay = max(0, int(delay_seconds))
    except (TypeError, ValueError):
        delay = 5
    try:
        subprocess.Popen(["shutdown", "/r", "/t", str(delay)])
        logger.info("[SYSTEM] Restart scheduled in %ds", delay)
        return f"Restarting in {delay} seconds. Run cancel_power_action to abort."
    except Exception as e:  # noqa: BLE001
        return f"ERROR: could not restart: {e}"


@tool(
    name="sleep_computer",
    description="Put the computer to sleep. Only call after the user has confirmed.",
    params={},
    dangerous=True,
    category="system",
    verification={"family": "none"},
)
def sleep_computer() -> str:
    try:
        subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], timeout=5)
        return "Putting the computer to sleep."
    except Exception as e:  # noqa: BLE001
        return f"ERROR: could not sleep: {e}"


@tool(
    name="cancel_power_action",
    description="Cancel a pending shutdown or restart that was scheduled with a delay.",
    params={},
    category="system",
    verification={"family": "none"},
)
def cancel_power_action() -> str:
    try:
        subprocess.run(["shutdown", "/a"], timeout=5)
        return "Cancelled the pending shutdown/restart."
    except Exception as e:  # noqa: BLE001
        return f"ERROR: nothing to cancel or it failed: {e}"


@tool(
    name="camera_control",
    description=(
        "Turn the camera ON or OFF for desktop apps by flipping the Windows "
        "camera privacy switch (per-user, no admin needed). Use this for "
        "'camera band karo' / 'camera off karo' / 'disable camera' / 'turn "
        "camera on'. This actually changes the setting -- it does NOT just open "
        "the Settings page."
    ),
    params={
        "action": {
            "type": "string",
            "description": "'on' to allow camera access, 'off' to block it.",
        }
    },
    category="system",
    verification={"family": "toggle", "capability": "privacy.camera"},
)
def camera_control(action: str = "") -> str:
    act = (action or "").strip().lower()
    if act in ("on", "enable", "allow", "true", "1", "unblock"):
        want = "Allow"
    elif act in ("off", "disable", "block", "deny", "false", "0"):
        want = "Deny"
    else:
        return "ERROR: say 'on' to allow or 'off' to block the camera."
    try:
        import winreg  # Windows-only; lazy import keeps the module portable.
    except Exception as e:  # noqa: BLE001
        return f"ERROR: camera control is only available on Windows ({e})."
    key_path = (r"Software\Microsoft\Windows\CurrentVersion"
                r"\CapabilityAccessManager\ConsentStore\webcam")
    try:
        key = winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, key_path, 0,
            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
        )
        try:
            winreg.SetValueEx(key, "Value", 0, winreg.REG_SZ, want)
            # Self-verify: read it straight back so the reply is honest.
            got, _ = winreg.QueryValueEx(key, "Value")
        finally:
            winreg.CloseKey(key)
    except PermissionError:
        return ("ERROR: Windows blocked changing the camera switch (it may be "
                "locked by your organization).")
    except Exception as e:  # noqa: BLE001
        return f"ERROR: could not change the camera setting ({e})."
    if str(got) != want:
        return ("ERROR: the camera switch did not change (Windows may have "
                "reverted it).")
    state = "on (apps can use it)" if want == "Allow" else "off (apps blocked)"
    logger.info("[SYSTEM] Camera access set to %s", want)
    return f"Camera is now {state}."


@tool(
    name="app_volume",
    description=(
        "Change the volume of ONE application without touching the rest, or "
        "list which apps are currently playing audio. Use for 'turn Spotify "
        "down', 'mute Chrome', 'Spotify ka volume 20 karo'. To change the "
        "whole system volume use set_volume instead."
    ),
    params={
        "app": {"type": "string", "required": False,
                "description": "App name, e.g. 'Spotify' or 'Chrome'. Omit to list all apps playing audio."},
        "level": {"type": "int", "required": False,
                  "description": "Volume for that app, 0-100."},
        "mute": {"type": "boolean", "required": False,
                 "description": "True to mute this app, False to unmute."},
    },
    category="system",
    verification={"family": "query", "cacheable": False},
)
def app_volume(app: str = "", level: int = None, mute: bool = None) -> str:
    from app.services.agent.tools import _audio

    # No app named -> answer "what is playing?" instead of failing.
    if not (app or "").strip():
        try:
            sessions = _audio.list_app_sessions()
        except Exception as e:  # noqa: BLE001
            return f"ERROR: could not read app volumes ({e})."
        if not sessions:
            return "No application is using audio right now."
        lines = [f"{s['name']}: {s['volume']}%" + (" (muted)" if s["muted"] else "")
                 for s in sessions]
        return "Apps using audio:\n" + "\n".join(lines)

    if level is None and mute is None:
        # Named an app but asked for no change -> report its current state.
        try:
            sessions = _audio.list_app_sessions()
        except Exception as e:  # noqa: BLE001
            return f"ERROR: could not read app volumes ({e})."
        target = app.strip().lower()
        for s in sessions:
            if target in s["name"].lower():
                muted = " (muted)" if s["muted"] else ""
                return f"{s['name']} is at {s['volume']}%{muted}."
        names = ", ".join(s["name"] for s in sessions) or "none"
        return f"ERROR: '{app}' is not playing audio. Currently playing: {names}."

    if level is not None:
        try:
            level = int(level)
        except (TypeError, ValueError):
            return "ERROR: level must be a number between 0 and 100."
        if not 0 <= level <= 100:
            return "ERROR: level must be between 0 and 100."

    try:
        result = _audio.set_app_volume(app, level_0_100=level, mute=mute)
    except Exception as e:  # noqa: BLE001
        return f"ERROR: could not change the volume for '{app}' ({e})."

    if result is None:
        try:
            names = ", ".join(s["name"] for s in _audio.list_app_sessions()) or "none"
        except Exception:  # noqa: BLE001
            names = "unknown"
        return (f"ERROR: '{app}' is not playing audio, so it has no volume "
                f"control. Currently playing: {names}.")

    logger.info("[SYSTEM] %s volume -> %d%% muted=%s",
                result["name"], result["volume"], result["muted"])
    if mute is not None and level is None:
        return f"{result['name']} is now {'muted' if result['muted'] else 'unmuted'}."
    muted = " (muted)" if result["muted"] else ""
    return f"{result['name']} volume set to {result['volume']}%{muted}."
