"""Launches and tracks the subprocesses for registered mini programs.

Each program's stdout/stderr is redirected to a per-program log file so the
menu can offer a "View Log" action without keeping output in memory.
"""

import os
import shlex
import signal
import subprocess
import sys
from typing import Dict, Optional

from config import Program, KIND_PYTHON


class ProcessManager:
    def __init__(self):
        # program id -> Popen
        self._procs: Dict[str, subprocess.Popen] = {}

    def is_running(self, program_id: str) -> bool:
        proc = self._procs.get(program_id)
        if proc is None:
            return False
        if proc.poll() is None:
            return True
        # Finished: forget it so it can be relaunched.
        self._procs.pop(program_id, None)
        return False

    def _build_command(self, program: Program):
        """Return (args_list, use_shell) for subprocess."""
        extra = shlex.split(program.args) if program.args else []
        if program.kind == KIND_PYTHON:
            return [sys.executable, program.target, *extra], False
        # shell: run the command string as-is (supports `open -a`, pipes, etc.)
        cmd = program.target
        if extra:
            cmd = cmd + " " + program.args
        return cmd, True

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
                start_new_session=True,  # own process group, so we can kill children
            )
        except (OSError, ValueError) as e:
            return f"Failed to launch: {e}"

        self._procs[program.id] = proc
        return None

    def stop(self, program_id: str) -> Optional[str]:
        """Terminate the program's process group. Returns error string or None."""
        proc = self._procs.get(program_id)
        if proc is None or proc.poll() is not None:
            self._procs.pop(program_id, None)
            return "Not running."
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            try:
                proc.terminate()
            except OSError:
                pass
        self._procs.pop(program_id, None)
        return None

    def stop_all(self):
        for pid in list(self._procs.keys()):
            self.stop(pid)
