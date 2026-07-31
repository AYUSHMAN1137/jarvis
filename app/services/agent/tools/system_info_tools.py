"""
Extra system tools: battery, resource usage, processes, date/time, network,
Wi-Fi listing/connect, opening Windows Settings pages, the Recycle Bin, and
extra power actions (hibernate / sign out).

Design (same rules as the rest of JARVIS):
* Reliability #1 -- every heavy dependency (psutil) is imported lazily INSIDE
  the function and wrapped, so a missing package or an odd machine can never
  crash the agent loop or even the import of this module.
* No fragile new WinRT attribute names (that is exactly what broke Wi-Fi).
  We only use proven dependencies already in requirements.txt (psutil) plus
  built-in Windows commands (netsh / shutdown / powershell / ms-settings:)
  and the Python standard library.
* Honest -- when Windows has no reliable toggle API for something (battery
  saver, mobile hotspot), we open the exact Settings page instead of faking a
  result.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
from datetime import datetime

from app.services.agent.tool_registry import tool

logger = logging.getLogger("J.A.R.V.I.S")

# Avoid flashing a console window for the helper commands on Windows.
try:
    _NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
except Exception:  # noqa: BLE001
    _NO_WINDOW = 0


def _run(cmd, timeout: int = 8) -> subprocess.CompletedProcess:
    """Run a command list, capturing text output. Never flashes a window.
    Does not raise on a non-zero exit code -- the caller inspects it."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=_NO_WINDOW,
    )


# --------------------------------------------------------------------------- #
# Battery / power
# --------------------------------------------------------------------------- #
@tool(
    name="battery_status",
    description=(
        "Check the laptop battery: charge percent, whether it's charging, and "
        "the estimated time left. Use for 'battery kitni hai', 'kitna charge hai'."
    ),
    params={},
    category="system",
    verification={"family": "query", "cacheable": False},
)
def battery_status() -> str:
    try:
        import psutil
    except Exception as e:  # noqa: BLE001
        return f"ERROR: battery info needs the 'psutil' package ({e})."
    try:
        batt = psutil.sensors_battery()
    except Exception as e:  # noqa: BLE001
        return f"I couldn't read the battery status ({e})."
    if batt is None:
        return "This device has no battery (likely a desktop) or it can't be read."
    pct = int(round(batt.percent))
    plugged = bool(batt.power_plugged)
    state = "charging" if plugged else "on battery"
    msg = f"Battery: {pct}% ({state})"
    secs = batt.secsleft
    if not plugged and isinstance(secs, int) and secs >= 0:
        hours, mins = divmod(secs // 60, 60)
        msg += f", about {hours}h {mins}m left"
    return msg + "."


# --------------------------------------------------------------------------- #
# Resource usage / processes
# --------------------------------------------------------------------------- #
@tool(
    name="system_resources",
    description=(
        "Read live CPU usage, RAM usage, and system-drive disk usage. "
        "Use for 'cpu kitna use ho raha hai', 'ram', 'disk space kitni hai'."
    ),
    params={},
    category="system",
    verification={"family": "query", "cacheable": False},
)
def system_resources() -> str:
    try:
        import psutil
    except Exception as e:  # noqa: BLE001
        return f"ERROR: system stats need the 'psutil' package ({e})."
    try:
        cpu = psutil.cpu_percent(interval=0.3)
        mem = psutil.virtual_memory()
        drive = os.environ.get("SystemDrive", "C:") + os.sep
        disk = psutil.disk_usage(drive)
        return (
            f"CPU: {cpu:.0f}% | "
            f"RAM: {mem.percent:.0f}% used "
            f"({mem.used / 1e9:.1f}/{mem.total / 1e9:.1f} GB) | "
            f"Disk {drive}: {disk.percent:.0f}% used "
            f"({disk.free / 1e9:.0f} GB free)"
        )
    except Exception as e:  # noqa: BLE001
        return f"I couldn't read system resources ({e})."


@tool(
    name="list_processes",
    description=(
        "List the top running processes by memory use. Use for "
        "'kaun se apps chal rahe hain', 'sabse zyada memory kaun le raha hai'."
    ),
    params={
        "limit": {
            "type": "int",
            "description": "How many processes to show (default 8, max 25).",
            "required": False,
        }
    },
    category="system",
    verification={"family": "query", "cacheable": False},
)
def list_processes(limit: int = 8) -> str:
    try:
        import psutil
    except Exception as e:  # noqa: BLE001
        return f"ERROR: process list needs the 'psutil' package ({e})."
    try:
        n = max(1, min(int(limit), 25))
    except Exception:  # noqa: BLE001
        n = 8
    try:
        procs = []
        for p in psutil.process_iter(["name", "memory_info"]):
            try:
                mem = p.info.get("memory_info")
                rss = getattr(mem, "rss", 0) if mem else 0
                procs.append((p.info.get("name") or "?", rss))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        procs.sort(key=lambda x: x[1], reverse=True)
        lines = [f"{name}: {rss / 1e6:.0f} MB" for name, rss in procs[:n]]
        return "Top processes by memory:\n" + "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return f"I couldn't list processes ({e})."


@tool(
    name="kill_process",
    description=(
        "Force-close all processes with a given name (e.g. 'chrome', "
        "'notepad'). Use for 'chrome band kar do', 'kill notepad'. "
        "This force-quits the app, so unsaved work may be lost."
    ),
    params={
        "name": {
            "type": "string",
            "description": "Process/app name, with or without .exe (e.g. 'chrome').",
            "required": True,
        }
    },
    dangerous=True,
    category="system",
    verification={"family": "close"},
)
def kill_process(name: str = "") -> str:
    target = (name or "").strip()
    if not target:
        return "ERROR: tell me which app/process to close (e.g. 'chrome')."
    image = target if target.lower().endswith(".exe") else target + ".exe"
    try:
        res = _run(["taskkill", "/IM", image, "/F", "/T"], timeout=10)
    except Exception as e:  # noqa: BLE001
        return f"I couldn't end {target} ({e})."
    out = (res.stdout or "") + (res.stderr or "")
    if res.returncode == 0:
        return f"Ended all '{target}' processes."
    if "not found" in out.lower():
        return f"No running process named '{target}' was found."
    return f"Couldn't end '{target}': {out.strip() or 'unknown error'}"


# --------------------------------------------------------------------------- #
# Date / time (zero-dependency, always reliable)
# --------------------------------------------------------------------------- #
@tool(
    name="get_datetime",
    description=(
        "Get the current local date, day and time. Use for 'time kya hua hai', "
        "'aaj kya date hai', 'aaj kaun sa din hai'."
    ),
    params={},
    category="system",
    verification={"family": "query", "cacheable": False},
)
def get_datetime() -> str:
    now = datetime.now().astimezone()
    return now.strftime("%A, %d %B %Y, %I:%M %p %Z").strip()


# --------------------------------------------------------------------------- #
# Network
# --------------------------------------------------------------------------- #
@tool(
    name="network_info",
    description=(
        "Show basic network info: hostname, local IP, and the connected "
        "Wi-Fi network. Use for 'mera ip kya hai', 'kis wifi se connected hu'."
    ),
    params={},
    category="system",
    verification={"family": "query", "cacheable": False},
)
def network_info() -> str:
    parts = []
    try:
        host = socket.gethostname()
        parts.append(f"Hostname: {host}")
        try:
            ip = socket.gethostbyname(host)
            if ip:
                parts.append(f"Local IP: {ip}")
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass
    try:
        res = _run(["netsh", "wlan", "show", "interfaces"], timeout=6)
        for line in res.stdout.splitlines():
            s = line.strip()
            if s.lower().startswith("ssid") and not s.lower().startswith("bssid"):
                val = s.split(":", 1)
                if len(val) == 2 and val[1].strip():
                    parts.append(f"Wi-Fi network: {val[1].strip()}")
                    break
    except Exception:  # noqa: BLE001
        pass
    return " | ".join(parts) if parts else "I couldn't read the network info."


@tool(
    name="list_wifi_networks",
    description=(
        "List the Wi-Fi networks currently in range. Use for "
        "'kaun kaun se wifi available hain', 'nearby wifi dikhao'."
    ),
    params={},
    category="system",
    verification={"family": "query", "cacheable": False},
)
def list_wifi_networks() -> str:
    try:
        res = _run(["netsh", "wlan", "show", "networks"], timeout=8)
    except Exception as e:  # noqa: BLE001
        return f"I couldn't scan Wi-Fi networks ({e})."
    ssids = []
    for line in res.stdout.splitlines():
        s = line.strip()
        if s.upper().startswith("SSID ") and ":" in s:
            nm = s.split(":", 1)[1].strip()
            if nm and nm not in ssids:
                ssids.append(nm)
    if not ssids:
        return "No Wi-Fi networks found (is Wi-Fi turned on?)."
    return "Available Wi-Fi networks:\n" + "\n".join(f"- {x}" for x in ssids)


@tool(
    name="connect_wifi",
    description=(
        "Connect to a SAVED Wi-Fi network by name (Windows can only auto-join "
        "networks you've connected to before). Use for 'X wifi se connect karo'."
    ),
    params={
        "name": {
            "type": "string",
            "description": "The Wi-Fi network / profile name.",
            "required": True,
        }
    },
    category="system",
    verification={"family": "toggle", "capability": "network.wifi"},
)
def connect_wifi(name: str = "") -> str:
    profile = (name or "").strip()
    if not profile:
        return "ERROR: tell me the Wi-Fi network name to connect to."
    try:
        res = _run(
            ["netsh", "wlan", "connect", f"name={profile}", f"ssid={profile}"],
            timeout=12,
        )
    except Exception as e:  # noqa: BLE001
        return f"I couldn't connect to '{profile}' ({e})."
    out = (res.stdout or "") + (res.stderr or "")
    if res.returncode == 0 and "completed successfully" in out.lower():
        return f"Connecting to '{profile}'..."
    if "is not found" in out.lower() or "no profile" in out.lower():
        return (
            f"No saved profile for '{profile}'. Windows can only auto-connect to "
            "a network you've connected to before. Connect once manually first."
        )
    return f"Couldn't connect to '{profile}': {out.strip() or 'unknown error'}"


# --------------------------------------------------------------------------- #
# Open a Windows Settings page (the honest bridge for things Windows has no
# reliable toggle API for, e.g. battery saver and mobile hotspot)
# --------------------------------------------------------------------------- #
_SETTINGS_URIS = {
    "wifi": "ms-settings:network-wifi",
    "bluetooth": "ms-settings:bluetooth",
    "airplane": "ms-settings:network-airplanemode",
    "hotspot": "ms-settings:network-mobilehotspot",
    "mobile hotspot": "ms-settings:network-mobilehotspot",
    "battery": "ms-settings:batterysaver",
    "battery saver": "ms-settings:batterysaver",
    "energy saver": "ms-settings:batterysaver",
    "display": "ms-settings:display",
    "brightness": "ms-settings:display",
    "sound": "ms-settings:sound",
    "volume": "ms-settings:sound",
    "power": "ms-settings:powersleep",
    "sleep": "ms-settings:powersleep",
    "network": "ms-settings:network-status",
    "vpn": "ms-settings:network-vpn",
    "night light": "ms-settings:nightlight",
    "notifications": "ms-settings:notifications",
    "storage": "ms-settings:storagesense",
    "apps": "ms-settings:appsfeatures",
    "privacy": "ms-settings:privacy",
    "camera": "ms-settings:privacy-webcam",
    "webcam": "ms-settings:privacy-webcam",
    "microphone": "ms-settings:privacy-microphone",
    "location": "ms-settings:privacy-location",
    "windows update": "ms-settings:windowsupdate",
    "settings": "ms-settings:",
}


@tool(
    name="open_settings_page",
    description=(
        "Open a specific Windows Settings page (e.g. battery saver, mobile "
        "hotspot, wifi, bluetooth, display, sound, power). Use this when the "
        "user wants to change a setting Windows has no safe toggle for, like "
        "'battery saver on karo' or 'hotspot kholo' -- it takes them one tap away."
    ),
    params={
        "page": {
            "type": "string",
            "description": (
                "Which page: wifi, bluetooth, airplane, hotspot, battery saver, "
                "display, sound, power, network, vpn, night light, storage, etc."
            ),
            "required": True,
        }
    },
    category="system",
    verification={"family": "open"},
)
def open_settings_page(page: str = "") -> str:
    key = (page or "").strip().lower()
    uri = _SETTINGS_URIS.get(key)
    if not uri and key:
        for k, v in _SETTINGS_URIS.items():
            if key in k or k in key:
                uri = v
                break
    if not uri:
        opts = ", ".join(sorted(set(_SETTINGS_URIS)))
        return f"I don't know that settings page. Try one of: {opts}."
    try:
        try:
            os.startfile(uri)  # type: ignore[attr-defined]  # Windows-only
        except AttributeError:
            _run(["cmd", "/c", "start", "", uri], timeout=6)
        return f"Opened the Windows Settings page for '{page}'."
    except Exception as e:  # noqa: BLE001
        return f"I couldn't open that settings page ({e})."


# --------------------------------------------------------------------------- #
# Recycle Bin + extra power actions
# --------------------------------------------------------------------------- #
@tool(
    name="empty_recycle_bin",
    description="Empty the Windows Recycle Bin. Use for 'recycle bin khali karo'.",
    params={},
    dangerous=True,
    category="system",
    verification={"family": "none"},
)
def empty_recycle_bin() -> str:
    try:
        res = _run(
            [
                "powershell", "-NoProfile", "-Command",
                "Clear-RecycleBin -Force -ErrorAction Stop",
            ],
            timeout=20,
        )
    except Exception as e:  # noqa: BLE001
        return f"I couldn't empty the Recycle Bin ({e})."
    out = (res.stdout or "") + (res.stderr or "")
    if res.returncode == 0:
        return "Recycle Bin emptied."
    if "empty" in out.lower():
        return "The Recycle Bin is already empty."
    return f"Couldn't empty the Recycle Bin: {out.strip() or 'unknown error'}"


@tool(
    name="hibernate_computer",
    description="Put the computer into hibernation. Use for 'hibernate karo'.",
    params={},
    dangerous=True,
    category="system",
    verification={"family": "none"},
)
def hibernate_computer() -> str:
    try:
        res = _run(["shutdown", "/h"], timeout=8)
    except Exception as e:  # noqa: BLE001
        return f"I couldn't hibernate ({e})."
    if res.returncode == 0:
        return "Hibernating..."
    out = (res.stdout or "") + (res.stderr or "")
    return (
        "Couldn't hibernate -- it may be disabled on this PC "
        "(enable it once with 'powercfg /hibernate on'). " + out.strip()
    )


@tool(
    name="sign_out",
    description="Sign out / log off the current Windows user. Use for 'log out karo'.",
    params={},
    dangerous=True,
    category="system",
    verification={"family": "none"},
)
def sign_out() -> str:
    try:
        _run(["shutdown", "/l"], timeout=6)
        return "Signing out..."
    except Exception as e:  # noqa: BLE001
        return f"I couldn't sign out ({e})."
