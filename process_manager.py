"""Launches and tracks the subprocesses for registered mini programs.

Tracking is PID-based and persisted to ~/.mini_app_navigator/running.json so
that:
  * quitting the menu bar app leaves the programs running, and
  * relaunching the app re-adopts the still-running processes (grabs them back).

Each program's stdout/stderr is redirected to a per-program log file so the
menu / dashboard can show output without keeping it in memory.
"""

import json
import os
import shlex
import signal
import subprocess
import sys
import time
from typing import Dict, Optional

from config import Program, KIND_PYTHON, RUNNING_PATH

# How long a restart waits for the old process to exit before relaunching.
RESTART_WAIT_SECONDS = 5.0


class ProcessManager:
    def __init__(self):
        # program id -> {"pid": int, "pgid": int}
        self._state: Dict[str, dict] = {}
        self._load()

    # ---------- persistence ----------

    def _load(self):
        try:
            with open(RUNNING_PATH) as f:
                self._state = json.load(f)
        except (OSError, json.JSONDecodeError):
            self._state = {}
        # Drop entries whose process is no longer alive (grab back the rest).
        changed = False
        for pid_key in list(self._state):
            if not self._alive(self._state[pid_key].get("pid", -1)):
                self._state.pop(pid_key)
                changed = True
        if changed:
            self._save()

    def _save(self):
        try:
            with open(RUNNING_PATH, "w") as f:
                json.dump(self._state, f)
        except OSError:
            pass

    # ---------- process helpers ----------

    @staticmethod
    def _alive(pid: int) -> bool:
        if pid is None or pid < 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # exists but owned by another user
        return True

    def is_running(self, program_id: str) -> bool:
        entry = self._state.get(program_id)
        if not entry:
            return False
        if self._alive(entry.get("pid", -1)):
            return True
        self._state.pop(program_id, None)
        self._save()
        return False

    def _build_command(self, program: Program):
        """Return (args_or_cmd, use_shell) for subprocess."""
        extra = shlex.split(program.args) if program.args else []
        if program.kind == KIND_PYTHON:
            return [sys.executable, program.target, *extra], False
        cmd = program.target
        if program.args:
            cmd = cmd + " " + program.args
        return cmd, True

    # ---------- actions ----------

    def launch(self, program: Program) -> Optional[str]:
        """Start the program. Returns None on success, or an error string."""
        if self.is_running(program.id):
            return "Already running."

        command, use_shell = self._build_command(program)
        cwd = program.cwd or None
        if cwd and not os.path.isdir(cwd):
            return f"Working directory not found: {cwd}"

        try:
            log_file = open(program.log_path(), "a", buffering=1)
            log_file.write(f"\n=== launch: {program.name} ===\n")
            log_file.flush()
            proc = subprocess.Popen(
                command,
                shell=use_shell,
                cwd=cwd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,  # own process group; detaches from us
            )
        except (OSError, ValueError) as e:
            return f"Failed to launch: {e}"
        finally:
            # The child inherited its own copy of the fd; release ours.
            try:
                log_file.close()
            except (OSError, UnboundLocalError):
                pass

        try:
            pgid = os.getpgid(proc.pid)
        except OSError:
            pgid = proc.pid
        self._state[program.id] = {"pid": proc.pid, "pgid": pgid}
        self._save()
        return None

    def stop(self, program_id: str) -> Optional[str]:
        """Terminate the program's process group. Returns error string or None."""
        entry = self._state.get(program_id)
        if not entry or not self._alive(entry.get("pid", -1)):
            self._state.pop(program_id, None)
            self._save()
            return "Not running."
        pgid = entry.get("pgid", entry["pid"])
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            try:
                os.kill(entry["pid"], signal.SIGTERM)
            except OSError:
                pass
        self._state.pop(program_id, None)
        self._save()
        return None

    def restart(self, program: Program) -> Optional[str]:
        """Stop the program (if running) and launch it again.

        Waits briefly for the old process to actually exit, so a program that
        holds an exclusive resource (a port, a lock file) can rebind it.
        Returns None on success, or an error string from the relaunch.
        """
        entry = self._state.get(program.id)
        pid = entry.get("pid", -1) if entry else -1
        self.stop(program.id)
        if pid > 0:
            deadline = time.monotonic() + RESTART_WAIT_SECONDS
            while self._alive(pid) and time.monotonic() < deadline:
                time.sleep(0.1)
            self.reap()
        return self.launch(program)

    def stop_all(self):
        for pid in list(self._state.keys()):
            self.stop(pid)

    def reap(self):
        """Best-effort reaping of finished child processes (avoids zombies).

        Only reaps our own children; re-adopted processes from a previous app
        run are not our children and are left untouched.
        """
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if pid == 0:
                break
