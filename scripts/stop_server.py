"""Stop any running J.A.R.V.I.S server process (run.py / uvicorn on port 8000).

Tries a graceful stop first so the FastAPI lifespan shutdown actually runs --
that is what saves chat sessions and checkpoints the SQLite write-ahead logs.
Falls back to a hard kill if the process does not exit in time.

    python scripts/stop_server.py            # graceful, then force after 10s
    python scripts/stop_server.py --force    # kill immediately
    python scripts/stop_server.py --timeout 20
"""

import os
import signal
import sys

try:
    import psutil
except ImportError:
    print("psutil not available")
    sys.exit(1)

GRACE_DEFAULT = 10.0


def _arg(flag: str, default: float) -> float:
    if flag in sys.argv:
        try:
            return float(sys.argv[sys.argv.index(flag) + 1])
        except (IndexError, ValueError):
            pass
    return default


def _find_servers():
    me = psutil.Process().pid
    found = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = " ".join(proc.info.get("cmdline") or [])
        except Exception:  # noqa: BLE001 - process vanished mid-iteration
            continue
        if "run.py" in cmdline and "python" in (proc.info.get("name") or "").lower():
            if proc.pid != me:
                found.append(proc)
    return found


def _request_graceful(proc) -> bool:
    """Ask the process to shut down. True if a request was actually delivered.

    Windows has no SIGTERM. CTRL_BREAK_EVENT is the closest equivalent and it
    only reaches processes started in their own process group, so this is
    best-effort by design -- the caller falls back to a kill.
    """
    try:
        if os.name == "nt":
            os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
        else:
            proc.terminate()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  graceful stop unavailable for pid {proc.pid}: {exc}")
        return False


def main() -> None:
    force = "--force" in sys.argv
    grace = _arg("--timeout", GRACE_DEFAULT)

    servers = _find_servers()
    if not servers:
        print("stopped: nothing was running")
        return

    stopped, forced = [], []

    if not force:
        asked = [p for p in servers if _request_graceful(p)]
        if asked:
            gone, alive = psutil.wait_procs(asked, timeout=grace)
            stopped.extend(p.pid for p in gone)
            asked_pids = {p.pid for p in asked}
            servers = alive + [p for p in servers if p.pid not in asked_pids]

    for proc in servers:
        try:
            proc.kill()
            forced.append(proc.pid)
        except psutil.NoSuchProcess:
            stopped.append(proc.pid)
        except Exception as exc:  # noqa: BLE001
            print(f"could not kill {proc.pid}: {exc}")

    if stopped:
        print(f"stopped gracefully: {stopped}")
    if forced:
        print(f"force-killed: {forced}")
        print("  lifespan shutdown did not run -- databases get checkpointed on next start")
    if not stopped and not forced:
        print("stopped: nothing was running")


if __name__ == "__main__":
    main()
