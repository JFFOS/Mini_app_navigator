"""Runs the timed launch/stop rules attached to registered mini programs.

A background thread wakes every few seconds and fires any rule whose slot has
come up. Two things make that reliable on a laptop rather than a server:

  * A rule that came due while the Mac was asleep, or while the navigator was
    being restarted, still fires when we next look — as long as the slot is
    less than GRACE_SECONDS old. Later than that it is skipped rather than
    fired at a surprising hour.
  * Every fired slot is written to ~/.mini_app_navigator/schedule_state.json,
    so restarting the navigator inside that grace window doesn't run the same
    rule a second time.

Firing is recorded in the program's own log file, so what the scheduler did and
what the program printed read as one story.
"""

import json
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from config import (Config, Program, Schedule, ACTION_LAUNCH, ACTION_STOP,
                    SCHEDULE_STATE_PATH)
from process_manager import ProcessManager

# How often the thread wakes to look for due rules.
TICK_SECONDS = 20.0
# How late a rule may fire after its slot (sleep, or the app being restarted).
GRACE_SECONDS = 15 * 60
# How far ahead next_run() will look for the following occurrence.
LOOKAHEAD_DAYS = 8


class Scheduler:
    """Owns the timer thread that applies each program's schedule rules."""

    def __init__(self, config: Config, procs: ProcessManager):
        self._config = config
        self._procs = procs
        # schedule id -> ISO timestamp of the slot it last fired for.
        self._fired: Dict[str, str] = {}
        self._thread: Optional[threading.Thread] = None
        self._wake = threading.Event()
        self._load()

    # ---------- persistence ----------

    def _load(self):
        try:
            with open(SCHEDULE_STATE_PATH) as f:
                data = json.load(f)
            self._fired = {k: str(v) for k, v in data.items() if isinstance(k, str)}
        except (OSError, json.JSONDecodeError, AttributeError):
            self._fired = {}

    def _save(self):
        try:
            with open(SCHEDULE_STATE_PATH, "w") as f:
                json.dump(self._fired, f)
        except OSError:
            pass

    def _forget_removed(self, live_ids: set):
        """Drop state for rules that no longer exist, so the file can't grow
        forever as rules are edited and replaced.

        Keys are "<rule id>:<phase>"; rule ids are hex, so the split is safe.
        """
        stale = [k for k in self._fired if k.split(":")[0] not in live_ids]
        for key in stale:
            self._fired.pop(key, None)
        return bool(stale)

    # ---------- thread ----------

    def start(self):
        if self._thread is not None:
            return
        self._wake.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._wake.set()
        self._thread = None

    def _run(self):
        while not self._wake.wait(TICK_SECONDS):
            try:
                self.tick()
            except Exception:
                # A scheduling bug must not take the timer thread down with it;
                # the next tick gets a clean try.
                pass

    # ---------- slot arithmetic ----------

    @staticmethod
    def _slots(schedule: Schedule, day: datetime) -> List[Tuple[str, str, datetime]]:
        """Every (phase, action, when) this rule produces for one weekday.

        A launch-only or stop-only rule contributes one slot; a window rule
        contributes both of its ends. The two phases are tracked separately so
        each fires — and is remembered — on its own.
        """
        if day.weekday() not in schedule.days:
            return []
        start = schedule.time_of_day()
        if start is None:
            return []
        midnight = day.replace(hour=0, minute=0, second=0, microsecond=0)
        first = midnight + timedelta(hours=start[0], minutes=start[1])
        if not schedule.is_window():
            return [("at", schedule.action, first)]
        end = schedule.until_time_of_day()
        if end is None:
            return []
        last = midnight + timedelta(hours=end[0], minutes=end[1])
        if last <= first:
            last += timedelta(days=1)   # 22:00 → 06:00 stops the next morning
        return [("at", ACTION_LAUNCH, first), ("until", ACTION_STOP, last)]

    def _fired_key(self, schedule: Schedule, phase: str) -> str:
        return f"{schedule.id}:{phase}"

    def _due_slots(self, schedule: Schedule,
                   now: datetime) -> List[Tuple[str, str, datetime]]:
        """Slots that are due and not yet fired, at most one per phase.

        Yesterday's weekday is considered as well as today's: a rule set for
        23:55 is still within grace shortly after midnight, when "today" is
        already the next day and may not even be one of the rule's weekdays —
        and a window that wraps midnight has its stop slot there too.
        """
        if not schedule.is_active():
            return []
        due, seen = [], set()
        for delta in (0, 1):
            for phase, action, slot in self._slots(schedule, now - timedelta(days=delta)):
                if phase in seen or slot > now:
                    continue
                if (now - slot).total_seconds() > GRACE_SECONDS:
                    continue
                if self._fired.get(self._fired_key(schedule, phase)) == slot.isoformat():
                    continue
                due.append((phase, action, slot))
                seen.add(phase)
        return due

    def next_run(self, program: Program) -> Optional[Tuple[datetime, Schedule, str]]:
        """The program's next upcoming (slot, rule, action), for display."""
        now = datetime.now().replace(second=0, microsecond=0)
        best = None
        for schedule in list(program.schedules):
            if not schedule.is_active():
                continue
            # Starts at yesterday: a window that wraps midnight hangs its stop
            # slot off the previous day, and that stop may still be ahead of us.
            for delta in range(-1, LOOKAHEAD_DAYS):
                upcoming = [(s, a) for _, a, s in
                            self._slots(schedule, now + timedelta(days=delta)) if s > now]
                if not upcoming:
                    continue
                slot, action = min(upcoming)
                if best is None or slot < best[0]:
                    best = (slot, schedule, action)
                break
        return best

    # ---------- firing ----------

    def tick(self, now: Optional[datetime] = None):
        """Fire every rule that has come due. Safe to call by hand."""
        now = now or datetime.now()
        changed = False
        live_ids = set()
        for program in list(self._config.programs):
            for schedule in list(program.schedules):
                live_ids.add(schedule.id)
                for phase, action, slot in self._due_slots(schedule, now):
                    # While paused, a due slot is still marked as fired: it is
                    # skipped, not deferred, so turning schedules back on can't
                    # set off whatever came due in the last 15 minutes.
                    if self._config.schedules_enabled:
                        self._fire(program, action, slot)
                    else:
                        self._log(program, f"=== scheduled {action} "
                                           f"({slot.strftime('%a %H:%M')}): "
                                           f"skipped, schedules paused ===")
                    self._fired[self._fired_key(schedule, phase)] = slot.isoformat()
                    changed = True
        if self._forget_removed(live_ids) or changed:
            self._save()

    def _fire(self, program: Program, action: str, slot: datetime):
        if action == ACTION_LAUNCH:
            err = self._procs.launch(program)
        else:
            err = self._procs.stop(program)
        late = (datetime.now() - slot).total_seconds()
        when = slot.strftime("%a %H:%M")
        note = f"scheduled {action} ({when})"
        if late > 90:
            note += f", {int(late // 60)} min late"
        self._log(program, f"=== {note}: {err or 'ok'} ===")

    @staticmethod
    def _log(program: Program, line: str):
        try:
            with open(program.log_path(), "a") as f:
                f.write(f"\n{line}\n")
        except OSError:
            pass

    # ---------- display helpers ----------

    def summary(self, program: Program) -> str:
        """One line describing the program's schedule, for menu and dashboard."""
        active: List[Schedule] = [s for s in program.schedules if s.is_active()]
        if not active:
            return ""
        if not self._config.schedules_enabled:
            return "paused"
        upcoming = self.next_run(program)
        if upcoming is None:
            return " · ".join(s.label() for s in active)
        slot, _schedule, action = upcoming
        today = datetime.now().date()
        if slot.date() == today:
            when = slot.strftime("%H:%M")
        elif slot.date() == today + timedelta(days=1):
            when = slot.strftime("tomorrow %H:%M")
        else:
            when = slot.strftime("%a %H:%M")
        return f"{'Launch' if action == ACTION_LAUNCH else 'Stop'} {when}"
