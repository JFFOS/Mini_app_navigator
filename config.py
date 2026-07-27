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
