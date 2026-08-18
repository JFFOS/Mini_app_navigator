"""Persistent configuration for Mini App Navigator.

Stores the list of registered mini programs in a JSON file under the user's
home directory so it survives restarts and lives outside the project tree.
"""

import json
import os
import re
import uuid
from dataclasses import dataclass, asdict, field
from typing import List, Optional
from urllib.parse import urlparse

CONFIG_DIR = os.path.expanduser("~/.mini_app_navigator")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
LOG_DIR = os.path.join(CONFIG_DIR, "logs")
# Tracks PIDs of launched programs so they can be re-adopted after the app
# quits and reopens (programs keep running when the menu bar app exits).
RUNNING_PATH = os.path.join(CONFIG_DIR, "running.json")

# A leading "scheme:" that is a real scheme rather than a "host:port" colon —
# i.e. not followed by digits only ("localhost:8501" is a host, "file:/x" isn't).
SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:(?!\d)")
# Characters that never belong in a URL typed into the dashboard field; their
# presence means it's prose or a typo, not an address.
UNSAFE_URL_CHARS = set("\"'<>\\^`{}|")

# A liveness probe written as a bare port ("8787"), or with an explicit
# "port:" / "match:" prefix. Needed for programs whose command is only a
# launcher: it hands off to a detached process and exits, so the PID we
# spawned dies immediately even though the program is up.
PROBE_PORT_RE = re.compile(r"^(?:port:)?(\d{1,5})$")
PROBE_MATCH_RE = re.compile(r"^match:(.+)$", re.S)
# Hosts whose ports can be probed locally: a URL on any of these is served by
# a process on this machine.
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}
PROBE_PORT = "port"
PROBE_MATCH = "match"

# Program kinds
KIND_PYTHON = "python"   # run a .py file with the current interpreter
KIND_SHELL = "shell"     # run an arbitrary shell command / open an app


@dataclass
class Program:
    """A single registered mini program."""
    name: str
    kind: str                       # KIND_PYTHON or KIND_SHELL
    target: str                     # path to .py file, or shell command
    args: str = ""                  # extra arguments (space separated)
    cwd: str = ""                   # working directory (optional)
    url: str = ""                   # program's own web UI, if it serves one
    probe: str = ""                 # liveness probe: port, or match:<pattern>
    auto_launch: bool = False       # start automatically when the app starts
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Program":
        return Program(
            id=d.get("id") or uuid.uuid4().hex[:8],
            name=d.get("name", "Untitled"),
            kind=d.get("kind", KIND_SHELL),
            target=d.get("target", ""),
            args=d.get("args", ""),
            cwd=d.get("cwd", ""),
            url=d.get("url", ""),
            probe=d.get("probe", ""),
            auto_launch=bool(d.get("auto_launch", False)),
        )

    def log_path(self) -> str:
        return os.path.join(LOG_DIR, f"{self.id}.log")

    def dashboard_url(self) -> str:
        """The program's web UI as a browser-openable http(s) URL.

        Returns "" when no URL is configured, or when what was typed isn't
        something we're willing to hand to the browser: only http/https, and
        only with a plausible host (so a typo doesn't become a dead link).
        """
        u = self.url.strip()
        if not u or any(ch in UNSAFE_URL_CHARS or ch.isspace() for ch in u):
            return ""
        if not SCHEME_RE.match(u):
            u = "http://" + u  # bare "127.0.0.1:8501" / "localhost/app"
        if not u.lower().startswith(("http://", "https://")):
            return ""
        return u if urlparse(u).hostname else ""

    def _url_port(self) -> Optional[int]:
        """The port of this program's dashboard URL, when it is a local one.

        A program serving its own UI on loopback already tells us how to find
        it, so that doubles as a probe with nothing to configure. Restricted to
        loopback: a remote host's port says nothing about a local process.
        """
        u = self.dashboard_url()
        if not u:
            return None
        parsed = urlparse(u)
        if (parsed.hostname or "").lower() not in LOOPBACK_HOSTS:
            return None
        try:
            return parsed.port
        except ValueError:  # malformed port in the URL
            return None

    def probe_spec(self) -> Optional[tuple]:
        """The liveness probe as (PROBE_PORT, port) or (PROBE_MATCH, pattern).

        Falls back to the dashboard URL's port when no probe is set, so a
        program with a local web UI is tracked correctly out of the box.
        Returns None when there is nothing usable: no probe and no local URL,
        an out-of-range port, or a pattern that would be read as an option by
        pgrep. Tracking stays PID-only in that case.
        """
        p = self.probe.strip()
        if not p:
            port = self._url_port()
            return (PROBE_PORT, port) if port else None
        m = PROBE_PORT_RE.match(p)
        if m:
            port = int(m.group(1))
            return (PROBE_PORT, port) if 1 <= port <= 65535 else None
        m = PROBE_MATCH_RE.match(p)
        if not m:
            return None
        pattern = m.group(1).strip()
        if not pattern or pattern.startswith("-"):
            return None
        return PROBE_MATCH, pattern


class Config:
    """Loads and saves the list of programs."""

    def __init__(self):
        self.programs: List[Program] = []
        self._ensure_dirs()
        self.load()

    def _ensure_dirs(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        os.makedirs(LOG_DIR, exist_ok=True)

    def load(self):
        if not os.path.exists(CONFIG_PATH):
            self.programs = []
            return
        try:
            with open(CONFIG_PATH, "r") as f:
                data = json.load(f)
            self.programs = [Program.from_dict(d) for d in data.get("programs", [])]
        except (json.JSONDecodeError, OSError):
            self.programs = []

    def save(self):
        self._ensure_dirs()
        with open(CONFIG_PATH, "w") as f:
            json.dump({"programs": [p.to_dict() for p in self.programs]}, f, indent=2)

    def add(self, program: Program):
        self.programs.append(program)
        self.save()

    def remove(self, program_id: str):
        self.programs = [p for p in self.programs if p.id != program_id]
        self.save()

    def get(self, program_id: str) -> Optional[Program]:
        return next((p for p in self.programs if p.id == program_id), None)
