"""Launch all three Elixire Band remote agents with staggered starts."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AGENTS = ["receptionist", "intake", "brief"]
PID_FILE = ROOT / ".agent_pids"

_STAGGER_SECONDS = 12   # wait between starting each agent
_KILL_WAIT_SECONDS = 4  # wait after killing old processes


def _kill_old_agents() -> None:
    """Kill agent processes from a previous run (Windows + Unix compatible)."""
    if not PID_FILE.exists():
        return
    pids = PID_FILE.read_text().strip().split()
    killed = []
    for pid_str in pids:
        try:
            pid = int(pid_str)
            if sys.platform == "win32":
                result = subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True, timeout=5,
                )
                if result.returncode == 0:
                    killed.append(pid)
            else:
                import os, signal
                try:
                    os.kill(pid, signal.SIGTERM)
                    killed.append(pid)
                except ProcessLookupError:
                    pass
        except (ValueError, Exception):
            pass
    if killed:
        print(f"[run_all] killed old agent pids: {killed}")
        time.sleep(_KILL_WAIT_SECONDS)
    try:
        PID_FILE.unlink()
    except FileNotFoundError:
        pass


def main() -> None:
    _kill_old_agents()

    procs = []
    for i, role in enumerate(AGENTS):
        script = ROOT / role / "agent.py"
        print(f"[run_all] starting {role}…")
        procs.append(subprocess.Popen(
            [sys.executable, str(script)],
            cwd=str(ROOT / role),
        ))
        if i < len(AGENTS) - 1:
            print(f"[run_all] waiting {_STAGGER_SECONDS}s before next agent…")
            time.sleep(_STAGGER_SECONDS)

    # Save PIDs so the next run can kill these processes cleanly.
    PID_FILE.write_text(" ".join(str(p.pid) for p in procs))
    print(f"[run_all] all agents running (pids: {[p.pid for p in procs]}) — ctrl+c to stop")

    try:
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        print("\n[run_all] stopping agents…")
        for p in procs:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(p.pid)],
                    capture_output=True,
                )
            else:
                p.terminate()
        try:
            PID_FILE.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
