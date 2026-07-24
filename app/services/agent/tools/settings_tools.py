"""
System settings / toggles tools (Windows): Wi-Fi, Bluetooth, and a combined
read of volume / mute / brightness / radios.

Design (matches the Master Plan rules):
* Reliability #1 -- every read/toggle is wrapped; a failure returns a clear
  message or ``None`` and never crashes the caller or the watcher.
* No hardcode -- Wi-Fi / Bluetooth are toggled through the Windows Runtime
  ``Radios`` API (no admin needed, no per-device assumptions). Wi-Fi connection
  info is read generically from ``netsh``.
* Privacy -- everything is local; nothing is uploaded.

The pure ``read_system_status()`` helper is also consumed by the background
watcher so the live world-model knows the current toggle state.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from typing import Optional

from app.services.agent.tool_registry import tool
from app.services.agent.automation import get_uia_engine

logger = logging.getLogger("J.A.R.V.I.S")


# --------------------------------------------------------------------------- #
# async helper (Windows Runtime calls are async)
# --------------------------------------------------------------------------- #
def _run_async(coro):
    """Run an async coroutine to completion from sync code, in any thread."""
    try:
        return asyncio.run(coro)
    except RuntimeError:
        # A loop is already running in this thread -> use a fresh one.
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


# --------------------------------------------------------------------------- #
# radios (Wi-Fi / Bluetooth) via Windows.Devices.Radios -- no admin needed
# --------------------------------------------------------------------------- #
def _radios_api():
    """Import the WinRT Radios classes, trying both package names.

    Different environments ship the WinRT bindings under different names:
    ``winsdk`` (older) or ``winrt`` (newer pywinrt). Importing whichever is
    present makes Wi-Fi / Bluetooth control work without pinning one package.
    Raises ImportError with an actionable message if neither is installed.
    """
    try:
        from winsdk.windows.devices.radios import Radio, RadioKind, RadioState
        return Radio, RadioKind, RadioState
    except Exception:
        pass
    try:
        from winrt.windows.devices.radios import Radio, RadioKind, RadioState
        return Radio, RadioKind, RadioState
    except Exception as e:  # noqa: BLE001
        raise ImportError(
            "Windows radio control needs the 'winsdk' package "
            "(run: pip install winsdk)."
        ) from e


def _wifi_kind(RadioKind):
    """Resolve the Wi-Fi RadioKind member tolerantly.

    WinRT exposes Wi-Fi as ``WI_FI`` on most builds, but some bindings use
    ``WIFI``. Pick whichever exists so a naming difference never crashes
    (this is exactly what broke Wi-Fi control before).
    """
    return getattr(RadioKind, "WI_FI", None) or getattr(RadioKind, "WIFI", None)


def _radio_kind(kind_name: str):
    _Radio, RadioKind, _RadioState = _radios_api()
    return _wifi_kind(RadioKind) if kind_name == "wifi" else RadioKind.BLUETOOTH


def _get_radios():
    """Return the list of system radios, or [] if unavailable."""
    Radio, _RadioKind, _RadioState = _radios_api()

    async def _inner():
        try:
            await Radio.request_access_async()
        except Exception:  # noqa: BLE001 - access prompt may already be settled
            pass
        return list(await Radio.get_radios_async())

    return _run_async(_inner())


def _radio_state(kind_name: str) -> Optional[str]:
    """Return 'on' / 'off' for the given radio, or None if it can't be read."""
    try:
        _Radio, RadioKind, RadioState = _radios_api()
        target = _wifi_kind(RadioKind) if kind_name == "wifi" else RadioKind.BLUETOOTH
        for r in _get_radios():
            if r.kind == target:
                return "on" if r.state == RadioState.ON else "off"
    except Exception as e:  # noqa: BLE001
        logger.warning("[SETTINGS][DIAG] read %s radio failed: %r", kind_name, e)
    return None


def _radio_access_allowed(status) -> bool:
    """True if a RadioAccessStatus means the change was actually allowed.

    Portable across winsdk/winrt: the value may be an enum (name
    'Allowed'/'ALLOWED') or a plain int (RadioAccessStatus.Allowed == 1).
    """
    try:
        if "allow" in str(getattr(status, "name", "")).lower():
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        return int(status) == 1
    except Exception:  # noqa: BLE001
        return False


def _set_radio(kind_name: str, on: bool) -> str:
    """Turn a radio on/off and report what Windows actually did.

    Returns one of:
      "ok"            -> the radio was switched
      "not_found"     -> no radio of this kind exists on this machine
      "denied:<why>"  -> Windows refused (e.g. DeniedBySystem / DeniedByUser)
      "error:<msg>"   -> the set call raised
    We MUST inspect the RadioAccessStatus returned by set_state_async: on many
    laptops Bluetooth toggling is allowed but Wi-Fi is denied by the system,
    and silently assuming success would be a lie to the user.
    """
    Radio, RadioKind, RadioState = _radios_api()
    target = _wifi_kind(RadioKind) if kind_name == "wifi" else RadioKind.BLUETOOTH
    want = RadioState.ON if on else RadioState.OFF

    async def _inner():
        try:
            await Radio.request_access_async()
        except Exception as e:  # noqa: BLE001
            logger.warning("[SETTINGS][DIAG] %s request_access raised: %r", kind_name, e)
        result = "not_found"
        try:
            radios = list(await Radio.get_radios_async())
        except Exception as e:  # noqa: BLE001
            logger.warning("[SETTINGS][DIAG] %s get_radios raised: %r", kind_name, e)
            return f"error:get_radios:{e}"
        try:
            inventory = [
                (
                    str(getattr(r, "name", "?")),
                    str(getattr(getattr(r, "kind", None), "name", getattr(r, "kind", "?"))),
                    str(getattr(getattr(r, "state", None), "name", getattr(r, "state", "?"))),
                )
                for r in radios
            ]
        except Exception:  # noqa: BLE001
            inventory = "<unreadable>"
        logger.info(
            "[SETTINGS][DIAG] %s set->%s | %d radio(s) found: %s",
            kind_name, "on" if on else "off", len(radios), inventory,
        )
        matched = False
        for r in radios:
            if r.kind == target:
                matched = True
                try:
                    status = await r.set_state_async(want)
                except Exception as e:  # noqa: BLE001
                    logger.warning("[SETTINGS][DIAG] %s set_state raised: %r", kind_name, e)
                    return f"error:{e}"
                status_name = getattr(status, "name", None) or str(status)
                logger.info(
                    "[SETTINGS][DIAG] %s set_state returned RadioAccessStatus=%s",
                    kind_name, status_name,
                )
                if _radio_access_allowed(status):
                    result = "ok"
                else:
                    result = f"denied:{status_name}"
        if not matched:
            logger.warning(
                "[SETTINGS][DIAG] %s: no radio of this kind among %d radio(s)",
                kind_name, len(radios),
            )
        logger.info("[SETTINGS][DIAG] %s final outcome = %s", kind_name, result)
        return result

    return str(_run_async(_inner()))


# --------------------------------------------------------------------------- #
# Wi-Fi connection info (generic, via netsh -- no admin needed for read)
# --------------------------------------------------------------------------- #
def _wifi_ssid() -> Optional[str]:
    try:
        out = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True, text=True, timeout=6,
        ).stdout
        for line in out.splitlines():
            s = line.strip()
            # Match the "SSID" line but not "BSSID".
            if s.lower().startswith("ssid") and not s.lower().startswith("bssid"):
                parts = s.split(":", 1)
                if len(parts) == 2 and parts[1].strip():
                    return parts[1].strip()
    except Exception as e:  # noqa: BLE001
        logger.debug("[SETTINGS] wifi ssid read failed: %s", e)
    return None


# --------------------------------------------------------------------------- #
# volume / brightness reads
# --------------------------------------------------------------------------- #
def _read_volume(use_cache: bool = False):
    """Return (level_percent:int|None, muted:bool|None).

    ``use_cache=True`` (background watcher) returns the last-known value WITHOUT
    creating a COM object -- the constant 2s COM churn was crashing the process.
    A live COM read only happens on an explicit, user-initiated call.
    """
    try:
        if use_cache:
            from app.services.agent.tools._audio import peek_volume

            return peek_volume()
        from app.services.agent.tools._audio import read_volume

        level, muted = read_volume()
        return level, muted
    except Exception as e:  # noqa: BLE001
        logger.debug("[SETTINGS] volume read failed: %s", e)
        return None, None


def _read_brightness() -> Optional[int]:
    try:
        import screen_brightness_control as sbc
        vals = sbc.get_brightness()
        if isinstance(vals, (list, tuple)) and vals:
            return int(vals[0])
        if isinstance(vals, (int, float)):
            return int(vals)
    except Exception as e:  # noqa: BLE001
        logger.debug("[SETTINGS] brightness read failed: %s", e)
    return None


def read_system_status(use_cache: bool = False) -> dict:
    """Best-effort snapshot of toggle state. Every field degrades to None/
    'unknown' instead of raising -- safe for the watcher to poll.

    ``use_cache=True`` (background watcher) skips the live COM volume read so the
    2s poll never creates COM objects. On-demand callers use the default (live).
    """
    level, muted = _read_volume(use_cache=use_cache)
    return {
        "volume": level,
        "muted": muted,
        "brightness": _read_brightness(),
        "wifi": _radio_state("wifi") or "unknown",
        "wifi_ssid": _wifi_ssid(),
        "bluetooth": _radio_state("bluetooth") or "unknown",
    }


# --------------------------------------------------------------------------- #
# tools
# --------------------------------------------------------------------------- #
def _toggle_tool(kind_name: str, label: str, action: str) -> str:
    act = (action or "").strip().lower()
    if act in ("status", "check", ""):
        state = _radio_state(kind_name)
        if state is None:
            return f"I couldn't read the {label} state on this system."
        extra = ""
        if kind_name == "wifi" and state == "on":
            ssid = _wifi_ssid()
            if ssid:
                extra = f" (connected to {ssid})"
        return f"{label} is {state}{extra}."
    if act in ("on", "enable", "true"):
        want = True
    elif act in ("off", "disable", "false"):
        want = False
    else:
        return f"ERROR: unknown action '{action}' (use on / off / status)."
    try:
        outcome = _set_radio(kind_name, want)
    except Exception as e:  # noqa: BLE001
        logger.warning("[SETTINGS][DIAG] %s _set_radio raised: %r", label, e)
        return (
            f"ERROR: could not change {label}: {e}. "
            "Make sure the 'winsdk' package is installed (pip install winsdk)."
        )
    logger.info("[SETTINGS][DIAG] %s toggle raw outcome = %r", label, outcome)
    if outcome == "ok":
        logger.info("[SETTINGS] %s turned %s", label, "on" if want else "off")
        return f"{label} turned {'on' if want else 'off'}."
    if outcome == "not_found":
        return (
            f"No controllable {label} radio was found on this system. "
            f"This adapter may not support software {label} toggling."
        )
    # denied:<why> or error:<msg> -- be honest about the real reason
    detail = outcome.split(":", 1)[1] if ":" in outcome else outcome
    return (
        f"Windows refused to change {label} ({detail}). "
        "First check Settings > Privacy & security > Radios and turn ON "
        "'Let apps control radios'. If that's already on, then this "
        f"{label} adapter doesn't allow software control -- use the "
        "hardware/Fn key or the Windows quick-settings toggle instead."
    )


@tool(
    name="wifi_control",
    description=(
        "Turn Wi-Fi on or off, or check whether Wi-Fi is on and what network "
        "it's connected to. Use for 'wifi on karo', 'wifi off', 'wifi on hai kya'."
    ),
    params={
        "action": {
            "type": "string",
            "description": "What to do: on, off, or status.",
            "enum": ["on", "off", "status"],
        }
    },
    category="system",
)
def wifi_control(action: str = "status") -> str:
    return _toggle_tool("wifi", "Wi-Fi", action)


@tool(
    name="bluetooth_control",
    description=(
        "Turn Bluetooth on or off, or check whether Bluetooth is on. "
        "Use for 'bluetooth on karo', 'bluetooth band karo', 'bluetooth on hai kya'."
    ),
    params={
        "action": {
            "type": "string",
            "description": "What to do: on, off, or status.",
            "enum": ["on", "off", "status"],
        }
    },
    category="system",
)
def bluetooth_control(action: str = "status") -> str:
    return _toggle_tool("bluetooth", "Bluetooth", action)


@tool(
    name="get_system_status",
    description=(
        "Read the current system settings state at once: volume level, mute, "
        "screen brightness, Wi-Fi (and connected network), and Bluetooth. "
        "Use for questions like 'volume kitna hai', 'brightness', 'wifi on hai kya'."
    ),
    params={},
    category="system",
)
def get_system_status() -> str:
    s = read_system_status()
    parts = []
    if s.get("volume") is not None:
        vol = f"Volume: {s['volume']}%"
        if s.get("muted"):
            vol += " (muted)"
        parts.append(vol)
    if s.get("brightness") is not None:
        parts.append(f"Brightness: {s['brightness']}%")
    wifi = s.get("wifi", "unknown")
    if wifi == "on" and s.get("wifi_ssid"):
        parts.append(f"Wi-Fi: on ({s['wifi_ssid']})")
    else:
        parts.append(f"Wi-Fi: {wifi}")
    parts.append(f"Bluetooth: {s.get('bluetooth', 'unknown')}")
    return " | ".join(parts) if parts else "Couldn't read system status."


# --------------------------------------------------------------------------- #
# Airplane (flight) mode -- Windows has no direct API, so we toggle every
# wireless radio. That is exactly what airplane mode does. Reuses the same
# verified Radios API as Wi-Fi/Bluetooth (no fragile new attribute names).
# --------------------------------------------------------------------------- #
def _set_all_radios(on: bool) -> str:
    """Turn every system radio on/off (effective airplane mode).

    Returns 'ok', 'partial:<ok>/<total>', 'none', 'denied', or 'error:<msg>'.
    """
    try:
        Radio, _RadioKind, RadioState = _radios_api()
    except Exception as e:  # noqa: BLE001
        logger.warning("[SETTINGS][DIAG] airplane radios_api failed: %r", e)
        return f"error:{e}"
    want = RadioState.OFF if on else RadioState.ON

    async def _inner():
        try:
            await Radio.request_access_async()
        except Exception as e:  # noqa: BLE001
            logger.warning("[SETTINGS][DIAG] airplane request_access raised: %r", e)
        try:
            radios = list(await Radio.get_radios_async())
        except Exception as e:  # noqa: BLE001
            logger.warning("[SETTINGS][DIAG] airplane get_radios raised: %r", e)
            return f"error:get_radios:{e}"
        if not radios:
            logger.warning("[SETTINGS][DIAG] airplane: no radios found")
            return "none"
        ok = 0
        total = len(radios)
        for r in radios:
            name = str(getattr(r, "name", "?"))
            try:
                status = await r.set_state_async(want)
            except Exception as e:  # noqa: BLE001
                logger.warning("[SETTINGS][DIAG] airplane set_state raised on %s: %r", name, e)
                continue
            status_name = getattr(status, "name", None) or str(status)
            if _radio_access_allowed(status):
                ok += 1
            else:
                logger.info("[SETTINGS][DIAG] airplane radio %s -> %s", name, status_name)
        logger.info(
            "[SETTINGS][DIAG] airplane set->%s | %d/%d radios changed",
            "on" if on else "off", ok, total,
        )
        if ok == 0:
            return "denied"
        if ok < total:
            return f"partial:{ok}/{total}"
        return "ok"

    return str(_run_async(_inner()))


@tool(
    name="airplane_mode",
    description=(
        "Turn airplane / flight mode on or off. On = switch all wireless "
        "radios (Wi-Fi, Bluetooth, etc.) off; off = switch them back on. "
        "Use for 'aeroplane mode on karo', 'airplane mode off', 'flight mode on'."
    ),
    params={
        "action": {
            "type": "string",
            "description": "on or off.",
            "enum": ["on", "off"],
        }
    },
    category="system",
)
def airplane_mode(action: str = "on") -> str:
    act = (action or "").strip().lower()
    if act in ("on", "enable", "true"):
        on = True
    elif act in ("off", "disable", "false"):
        on = False
    else:
        return f"ERROR: unknown action '{action}' (use on / off)."
    outcome = _set_all_radios(on)
    logger.info("[SETTINGS][DIAG] airplane_mode raw outcome = %r", outcome)
    if outcome == "ok":
        return f"Airplane mode turned {'on' if on else 'off'}."
    if outcome.startswith("partial:"):
        return (
            f"Airplane mode partly applied ({outcome.split(':', 1)[1]} radios). "
            "Some radios may be locked by a hardware switch."
        )
    if outcome == "none":
        return "No wireless radios were found on this device."
    if outcome == "denied":
        return (
            "Windows refused to change the radios. Check Settings > Privacy & "
            "security > Radios ('Let apps control radios')."
        )
    return f"I couldn't change airplane mode ({outcome})."


# --------------------------------------------------------------------------- #
# Windows Update -- dedicated tool so the agent doesn't need a 3-step chain
# --------------------------------------------------------------------------- #
@tool(
    name="check_for_updates",
    description=(
        "Check for Windows updates. Opens the Windows Update settings page and "
        "clicks the 'Check for updates' button automatically. Use this when the "
        "user says 'check for updates', 'update my PC', 'download updates', "
        "'windows update', etc."
    ),
    params={},
    category="system",
)
def check_for_updates() -> str:
    """Open Windows Update settings and click 'Check for updates' via UIA."""
    # Step 1: Open the Windows Update page directly (deep link).
    try:
        subprocess.Popen(
            ["cmd", "/c", "start", "ms-settings:windowsupdate"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: Could not open Windows Update settings: {exc}"

    # Step 2: Give the Settings page time to fully render.
    time.sleep(2.5)

    # Step 3: Click the 'Check for updates' button via UI Automation.
    eng = get_uia_engine()

    # Try the most common button names (varies by Windows version/locale).
    button_names = [
        "Check for updates",
        "Check for Updates",
        "Download",
        "Download and install",
    ]
    for btn_name in button_names:
        res = eng.click(btn_name, window="Settings")
        if res.get("ok"):
            evidence = res.get("evidence") or f"Clicked '{btn_name}'."
            return f"Windows Update page opened and I clicked '{btn_name}'. {evidence}"

    # If none of the known names worked, list what IS visible so the LLM
    # can decide the next step (e.g. updates already up-to-date, or the
    # button has a different label on this Windows build).
    listing = eng.list_controls(window="Settings")
    if listing.get("ok"):
        controls = listing.get("controls") or []
        names = [c.get("name") for c in controls[:15] if c.get("name")]
        if names:
            return (
                "I opened Windows Update settings but couldn't find a "
                f"'Check for updates' button. Visible controls: {', '.join(names)}. "
                "The system may already be up to date."
            )

    return (
        "I opened the Windows Update settings page, but I couldn't find or "
        "click the 'Check for updates' button. The page may still be loading "
        "or your Windows version uses a different layout."
    )
