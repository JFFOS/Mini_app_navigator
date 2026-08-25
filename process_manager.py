"""Launches and tracks the subprocesses for registered mini programs.

Tracking is PID-based and persisted to ~/.mini_app_navigator/running.json so
that:
  * quitting the menu bar app leaves the programs running, and
  * relaunching the app re-adopts the still-running processes (grabs them back).

PID tracking alone can't see a program whose command is only a *launcher* —
`open -a Foo.app`, or a script that does `nohup … &` and returns. Those hand
off to a detached process and exit, so the PID we spawned is gone a second
later while the program itself is up. A program can declare a liveness probe
(a listening port, or a pgrep pattern); when the tracked PID is dead, the probe
is consulted and the process it finds is adopted, so status, Stop and Restart
all work on the real process.

Each program's stdout/stderr is redirected to a per-program log file so the
menu / dashboard can show output without keeping it in memory.
"""

import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import time
from typing import Dict, Optional, Tuple

from config import Program, KIND_PYTHON, RUNNING_PATH, PROBE_PORT

# How long a restart waits for the old process to exit before relaunching.
RESTART_WAIT_SECONDS = 5.0
# Probes shell out, and status is polled every few seconds by both the menu and
# the dashboard. Cache results briefly so one refresh tick costs one probe.
PROBE_CACHE_SECONDS = 2.0
# Probe helpers are local and fast; if one hangs, treat the program as down
# rather than freezing the menu bar.
PROBE_TIMEOUT_SECONDS = 3.0


class ProcessManager:
    def __init__(self):
        # program id -> {"pid": int, "pgid": int, "adopted": bool}
        # "adopted" marks a process found by a probe rather than spawned by us.
        self._state: Dict[str, dict] = {}
        # program id -> (expires_at, (alive, pid_or_None)) for recent probes.
        self._probe_cache: Dict[str, Tuple[float, Tuple[bool, Optional[int]]]] = {}
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

    # ---------- liveness probes ----------

    @staticmethod
    def _port_open(port: int) -> bool:
        """Cheap check that something is listening on 127.0.0.1:port."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.25)
            return s.connect_ex(("127.0.0.1", port)) == 0

    def _run_probe(self, spec: tuple) -> Tuple[bool, Optional[int]]:
        """Run a probe. Returns (is_alive, pid_if_identifiable).

        A port can be seen as busy without the owning PID being readable (lsof
        won't report a process owned by another user), so "alive" and "we know
        which process" are answered separately.
        """
        kind, value = spec
        if kind == PROBE_PORT:
            cmd = ["lsof", "-ti", f"tcp:{value}", "-sTCP:LISTEN"]
        else:
            cmd = ["pgrep", "-f", value]
        try:
            out = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=PROBE_TIMEOUT_SECONDS,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        # Never match ourselves: a pattern loose enough to hit the navigator
        # would make every program look permanently running.
        pids = []
        for line in out.split():
            try:
                pid = int(line)
            except ValueError:
                continue
            if pid != os.getpid() and pid > 0:
                pids.append(pid)
        if pids:
            # A loose pattern can also catch the launcher shell that's still
            # winding down; the lowest PID is the better bet for the long-lived
            # process, and a wrong pick self-corrects on the next poll when the
            # PID turns out to be dead.
            return True, min(pids)
        if kind == PROBE_PORT and self._port_open(value):
            return True, None
        return False, None

    def _probe(self, program: Program) -> Tuple[bool, Optional[int]]:
        spec = program.probe_spec()
        if not spec:
            return False, None
        cached = self._probe_cache.get(program.id)
        now = time.monotonic()
        if cached and now < cached[0]:
            return cached[1]
        result = self._run_probe(spec)
        self._probe_cache[program.id] = (now + PROBE_CACHE_SECONDS, result)
        return result

    def _adopt_via_probe(self, program: Program) -> bool:
        """Look for a detached process this program left behind.

        Records what it finds so status stays cheap afterwards (the tracked PID
        answers the next call) and so Stop has something to signal.
        """
        alive, pid = self._probe(program)
        if alive and pid:
            self._state[program.id] = {"pid": pid, "pgid": pid, "adopted": True}
            self._save()
        return alive

    # ---------- status ----------

    def is_running(self, program: Program) -> bool:
        entry = self._state.get(program.id)
        if entry and self._alive(entry.get("pid", -1)):
            return True
        if entry:
            self._state.pop(program.id, None)
            self._save()
        return self._adopt_via_probe(program)

    def is_adopted(self, program_id: str) -> bool:
        """True when the tracked process was found by a probe, not launched."""
        return bool(self._state.get(program_id, {}).get("adopted"))

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
        if self.is_running(program):
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
        self._state[program.id] = {"pid": proc.pid, "pgid": pgid, "adopted": False}
        self._probe_cache.pop(program.id, None)
        self._save()
        return None

    def stop(self, program: Program) -> Optional[str]:
        """Terminate the program. Returns error string or None.

        A probe may report a program as up without naming a PID, in which case
        there is nothing here to signal — say so rather than silently doing
        nothing while the status keeps reading "running".
        """
        if not self.is_running(program):
            return "Not running."
        entry = self._state.get(program.id)
        if not entry:
            return ("Running, but the navigator can't identify its process — "
                    "stop it from the program itself.")
        self._terminate(entry)
        self._state.pop(program.id, None)
        self._probe_cache.pop(program.id, None)
        self._save()
        return None

    def _terminate(self, entry: dict):
        """SIGTERM the tracked process.

        Processes we launched get the whole process group, since the command
        may have spawned helpers of its own. An adopted process is signalled
        alone: we didn't create its group, so its other members aren't ours to
        kill.
        """
        pid = entry.get("pid", -1)
        if entry.get("adopted"):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
            return
        pgid = entry.get("pgid", pid)
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass

    def restart(self, program: Program) -> Optional[str]:
        """Stop the program (if running) and launch it again.

        Waits briefly for the old process to actually exit, so a program that
        holds an exclusive resource (a port, a lock file) can rebind it.
        Returns None on success, or an error string from the relaunch.
        """
        self.is_running(program)  # adopt first, so there's a PID to stop
        entry = self._state.get(program.id)
        pid = entry.get("pid", -1) if entry else -1
        err = self.stop(program)
        if err and err != "Not running.":
            return err
        deadline = time.monotonic() + RESTART_WAIT_SECONDS
        if pid > 0:
            while self._alive(pid) and time.monotonic() < deadline:
                time.sleep(0.1)
            self.reap()
        spec = program.probe_spec()
        if spec and spec[0] == PROBE_PORT:
            # The old process can outlive the SIGTERM long enough to still hold
            # the port; relaunching into a taken port just fails.
            while self._port_open(spec[1]) and time.monotonic() < deadline:
                time.sleep(0.1)
        self._probe_cache.pop(program.id, None)
        return self.launch(program)

    # ---------- bulk actions ----------

    def act_many(self, action: str, programs) -> list:
        """Apply launch / stop / restart to several programs — a whole group.

        Returns one "name: reason" string per program that actually failed.
        A launch of something already up, or a stop of something already down,
        is what "all of them" means for the rest of the group, so those are not
        reported as failures.
        """
        run = {"launch": self.launch, "stop": self.stop,
               "restart": self.restart}.get(action)
        if run is None:
            return []
        benign = {"Already running.", "Not running."}
        errors = []
        for program in programs:
            err = run(program)
            if err and err not in benign:
                errors.append(f"{program.name}: {err}")
        return errors

    def running_count(self, programs) -> int:
        return sum(1 for p in programs if self.is_running(p))

    def stop_all(self):
        """Stop everything currently tracked (probe-only programs excluded —
        stopping those needs the Program, which this doesn't have)."""
        for program_id, entry in list(self._state.items()):
            self._terminate(entry)
            self._state.pop(program_id, None)
        self._probe_cache.clear()
        self._save()

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
