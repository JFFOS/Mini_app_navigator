"""Persistent configuration for Mini App Navigator.

Stores the list of registered mini programs, and the groups they are gathered
into, in a JSON file under the user's home directory so it survives restarts and
lives outside the project tree.
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
# Remembers which schedule occurrence each rule last fired, so a rule doesn't
# fire twice for the same slot after the app is restarted or the Mac wakes.
SCHEDULE_STATE_PATH = os.path.join(CONFIG_DIR, "schedule_state.json")

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

# Schedule actions
ACTION_LAUNCH = "launch"
ACTION_STOP = "stop"
# One rule that does both: launch at `at`, stop again at `until`. Keeping the
# pair together means the days are picked once and can't drift apart.
ACTION_WINDOW = "window"
SCHEDULE_ACTIONS = (ACTION_LAUNCH, ACTION_STOP, ACTION_WINDOW)

# Weekdays as datetime.weekday() numbers them: Monday is 0.
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
WEEKDAYS = frozenset({0, 1, 2, 3, 4})
WEEKEND = frozenset({5, 6})
EVERY_DAY = frozenset(range(7))
# "9:00" and "09:00" both mean the same thing.
TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


@dataclass
class Schedule:
    """One timed rule: launch, stop, or both, at a time on given days.

    Days are stored as explicit weekday numbers rather than a preset name, so
    "daily", "weekdays", "weekends", "every Tuesday" and any hand-picked
    combination are all the same thing stored one way — the dashboard just
    offers the common ones as one-click presets.

    An ACTION_WINDOW rule runs the program between `at` and `until`. An `until`
    at or before `at` reads as overnight, so 22:00 → 06:00 stops the next
    morning rather than being rejected.
    """
    action: str = ACTION_LAUNCH     # ACTION_LAUNCH, ACTION_STOP or ACTION_WINDOW
    at: str = "09:00"               # HH:MM, local time — launch (or stop) time
    until: str = "18:00"            # HH:MM — stop time, ACTION_WINDOW only
    days: List[int] = field(default_factory=list)   # 0 = Monday … 6 = Sunday
    enabled: bool = True
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    @staticmethod
    def from_dict(d: dict) -> "Schedule":
        days = []
        for value in d.get("days", []):
            try:
                day = int(value)
            except (TypeError, ValueError):
                continue
            if 0 <= day <= 6 and day not in days:
                days.append(day)
        action = d.get("action")
        return Schedule(
            id=d.get("id") or uuid.uuid4().hex[:8],
            action=action if action in SCHEDULE_ACTIONS else ACTION_LAUNCH,
            at=d.get("at", "09:00"),
            until=d.get("until", "18:00"),
            days=sorted(days),
            enabled=bool(d.get("enabled", True)),
        )

    @staticmethod
    def _parse_time(value: str) -> Optional[tuple]:
        m = TIME_RE.match((value or "").strip())
        if not m:
            return None
        hour, minute = int(m.group(1)), int(m.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
        return None

    def time_of_day(self) -> Optional[tuple]:
        """(hour, minute) of `at`, or None when the time doesn't parse."""
        return self._parse_time(self.at)

    def until_time_of_day(self) -> Optional[tuple]:
        """(hour, minute) of `until`, or None. Meaningful for windows only."""
        return self._parse_time(self.until)

    def is_window(self) -> bool:
        return self.action == ACTION_WINDOW

    def wraps_midnight(self) -> bool:
        """True when a window's stop time lands on the following day."""
        start, end = self.time_of_day(), self.until_time_of_day()
        return bool(self.is_window() and start and end and end <= start)

    def is_valid(self) -> bool:
        """A rule with no days, or an unusable time, can never fire."""
        if not self.days or self.time_of_day() is None:
            return False
        if self.is_window():
            end = self.until_time_of_day()
            # Equal times would be a zero-length window — a stop landing on the
            # same minute as its own launch. Read as overnight instead.
            return end is not None
        return True

    def is_active(self) -> bool:
        return self.enabled and self.is_valid()

    def action_label(self) -> str:
        if self.action == ACTION_WINDOW:
            return "Run"
        return "Launch" if self.action == ACTION_LAUNCH else "Stop"

    def days_label(self) -> str:
        chosen = frozenset(self.days)
        if not chosen:
            return "no days"
        if chosen == EVERY_DAY:
            return "every day"
        if chosen == WEEKDAYS:
            return "weekdays"
        if chosen == WEEKEND:
            return "weekends"
        return ", ".join(DAY_NAMES[d] for d in sorted(chosen))

    def label(self) -> str:
        if self.is_window():
            overnight = " next day" if self.wraps_midnight() else ""
            return (f"Run {self.at} → {self.until}{overnight} · "
                    f"{self.days_label()}")
        return f"{self.action_label()} {self.at} · {self.days_label()}"


@dataclass
class Group:
    """A named set of mini programs, acted on together.

    Membership lives on the Program (a `group` id) rather than as a list of ids
    here, so a program can only ever be in one group and removing a program
    can't leave a dangling reference behind.

    `show_in_menu` hides the whole group — entry and members — from the menu bar
    without ungrouping or deleting anything, for programs that are managed from
    the dashboard and only clutter the menu.
    """
    name: str
    show_in_menu: bool = True
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Group":
        return Group(
            id=d.get("id") or uuid.uuid4().hex[:8],
            name=d.get("name", "Untitled group"),
            show_in_menu=bool(d.get("show_in_menu", True)),
        )


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
    group: str = ""                 # id of the Group it belongs to, if any
    schedules: List[Schedule] = field(default_factory=list)   # timed rules
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
            group=d.get("group", "") or "",
            schedules=[Schedule.from_dict(x) for x in d.get("schedules", [])
                       if isinstance(x, dict)],
        )

    def log_path(self) -> str:
        return os.path.join(LOG_DIR, f"{self.id}.log")

    def get_schedule(self, schedule_id: str) -> Optional["Schedule"]:
        return next((s for s in self.schedules if s.id == schedule_id), None)

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
    """Loads and saves the list of programs and the groups they belong to."""

    def __init__(self):
        self.programs: List[Program] = []
        self.groups: List[Group] = []
        # Master switch for every schedule rule, flipped from the menu bar.
        # Off pauses all rules without touching them, so they come back as-is.
        self.schedules_enabled: bool = True
        self._ensure_dirs()
        self.load()

    def _ensure_dirs(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        os.makedirs(LOG_DIR, exist_ok=True)

    def load(self):
        if not os.path.exists(CONFIG_PATH):
            self.programs = []
            self.groups = []
            self.schedules_enabled = True
            return
        try:
            with open(CONFIG_PATH, "r") as f:
                data = json.load(f)
            self.programs = [Program.from_dict(d) for d in data.get("programs", [])]
            self.groups = [Group.from_dict(d) for d in data.get("groups", [])]
            self.schedules_enabled = bool(data.get("schedules_enabled", True))
        except (json.JSONDecodeError, OSError):
            self.programs = []
            self.groups = []
            self.schedules_enabled = True
        # A config edited by hand (or written by an older version) can name a
        # group that isn't there; such a program is ungrouped rather than
        # invisible in a group that never renders.
        known = {g.id for g in self.groups}
        for program in self.programs:
            if program.group and program.group not in known:
                program.group = ""

    def save(self):
        self._ensure_dirs()
        with open(CONFIG_PATH, "w") as f:
            json.dump({
                "programs": [p.to_dict() for p in self.programs],
                "groups": [g.to_dict() for g in self.groups],
                "schedules_enabled": self.schedules_enabled,
            }, f, indent=2)

    def add(self, program: Program):
        self.programs.append(program)
        self.save()

    def remove(self, program_id: str):
        self.programs = [p for p in self.programs if p.id != program_id]
        self.save()

    def get(self, program_id: str) -> Optional[Program]:
        return next((p for p in self.programs if p.id == program_id), None)

    # ---------- groups ----------

    def add_group(self, group: Group):
        self.groups.append(group)
        self.save()

    def remove_group(self, group_id: str):
        """Delete a group. Its programs are kept, and become ungrouped."""
        self.groups = [g for g in self.groups if g.id != group_id]
        for program in self.programs:
            if program.group == group_id:
                program.group = ""
        self.save()

    def get_group(self, group_id: str) -> Optional[Group]:
        return next((g for g in self.groups if g.id == group_id), None)

    def group_programs(self, group_id: str) -> List[Program]:
        """The group's members, in the order the programs were added."""
        return [p for p in self.programs if p.group == group_id]

    def ungrouped_programs(self) -> List[Program]:
        return [p for p in self.programs if not p.group]
