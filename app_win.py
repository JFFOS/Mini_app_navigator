"""Mini App Navigator — Windows system-tray backend (built on pystray).

Selected automatically by app.py on Windows; see app_mac.py for the macOS
menu bar equivalent. The two backends offer the same actions, and share the
Config, ProcessManager, Scheduler and dashboard underneath.

Each registered program gets a submenu with Launch / Stop / Restart / View Log
actions. Programs can be gathered into groups; a group replaces its members in
the menu with one entry that acts on all of them together, and can be hidden
from the menu altogether. Adding, removing, and full management happen in a
local web dashboard (see dashboard.py) opened from the menu.

Two things differ from the macOS backend, both because of what a Windows tray
menu can do:

  * The menu is rebuilt from live state every time it is opened (pystray
    re-runs the menu callable on each display), so there is no refresh timer
    and no "Refresh" entry — the menu bar app has to redraw itself constantly,
    a tray menu only exists while it is open.
  * A tray item can't carry both a checkmark and a submenu, so a "✓" prefix on
    the label stands in for the checkmark that marks a running program.
"""

import os
import subprocess
import sys
import webbrowser

import pystray
from PIL import Image, ImageDraw

from config import Config, Group, Program, KIND_PYTHON
from process_manager import ProcessManager
from dashboard import DashboardServer
from scheduler import Scheduler
import login_item

# Label prefixes standing in for the menu bar's checkmark states.
MARK_RUNNING = "✓ "
MARK_PARTIAL = "◐ "     # a group with only some of its members up
MARK_STOPPED = "　"      # ideographic space: keeps labels aligned when idle


def _icon_image() -> Image.Image:
    """The tray icon, drawn at load time so the app needs no image asset.

    A rocket on a blue disc — the counterpart of the 🚀 the macOS backend puts
    in the menu bar.
    """
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, size - 4, size - 4), fill=(37, 99, 235, 255))
    d.polygon(
        [(size / 2, 12), (size - 16, size - 16), (size / 2, size - 26), (16, size - 16)],
        fill=(255, 255, 255, 255),
    )
    return img


class MiniAppNavigator:
    def __init__(self):
        self.config = Config()
        self.procs = ProcessManager()
        # The dashboard shares these exact instances, so menu and web UI stay
        # in sync automatically. It is started lazily (on first open) so the
        # HTTP server thread + socket cost nothing unless actually used.
        self.scheduler = Scheduler(self.config, self.procs)
        self.dashboard = DashboardServer(self.config, self.procs, self.scheduler)
        self._auto_launch()
        # Started after auto launch so a "launch" rule that is already within
        # its grace window doesn't race the startup launch of the same program.
        self.scheduler.start()
        self.icon = pystray.Icon(
            "mini_app_navigator", _icon_image(), "Mini Apps",
            # A single callable as the whole menu: pystray calls it each time
            # the menu is about to be shown, so it always reflects live state.
            menu=pystray.Menu(self._menu_items),
        )

    def _auto_launch(self):
        """Start programs flagged for auto launch, at app startup.

        Programs re-adopted from a previous run (still alive) are skipped, so a
        restart of the navigator doesn't start a second copy. Failures are
        logged to the program's own log rather than shown as a notification,
        which would stack one balloon per program before the tray icon exists.
        """
        for program in self.config.programs:
            if not program.auto_launch or self.procs.is_running(program):
                continue
            err = self.procs.launch(program)
            if err:
                try:
                    with open(program.log_path(), "a") as f:
                        f.write(f"\n=== auto launch failed: {err} ===\n")
                except OSError:
                    pass

    # ---------- menu construction ----------

    def _menu_items(self):
        # A grouped program is reached through its group, never listed again at
        # the top level — collapsing a set of programs into one entry is the
        # point of a group. A group set to hide takes its members with it.
        items = []
        for group in self.config.groups:
            if group.show_in_menu:
                items.append(self._group_item(group))
        for program in self.config.ungrouped_programs():
            items.append(self._program_item(program))
        if not items:
            hidden = bool(self.config.programs)
            items.append(pystray.MenuItem(
                "All programs are in hidden groups" if hidden
                else "No mini programs yet", None, enabled=False))

        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("Open Dashboard…", self._on_dashboard))
        items.append(pystray.MenuItem(
            "Launch at Login", self._on_toggle_login,
            checked=lambda item: login_item.is_enabled()))
        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("Restart Navigator", self._on_restart_self))
        items.append(pystray.MenuItem("Quit (programs keep running)", self._on_quit))
        return items

    def _group_item(self, group: Group) -> pystray.MenuItem:
        """A whole group as one menu entry: act on all of it, or drill in.

        The mark is "✓" when every member is running and "◐" when only some
        are, so the entry says at a glance whether "all of it" is actually the
        state of things.
        """
        members = self.config.group_programs(group.id)
        if not members:
            return pystray.MenuItem(MARK_STOPPED + group.name, pystray.Menu(
                pystray.MenuItem("(no programs in this group)", None, enabled=False)))

        running = self.procs.running_count(members)
        mark = (MARK_STOPPED if running == 0
                else MARK_RUNNING if running == len(members) else MARK_PARTIAL)
        entries = [
            pystray.MenuItem("Launch All", self._make_group_action(group, "launch")),
            pystray.MenuItem("Stop All", self._make_group_action(group, "stop")),
            pystray.MenuItem("Restart All", self._make_group_action(group, "restart")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(f"{running} of {len(members)} running", None, enabled=False),
            pystray.Menu.SEPARATOR,
        ]
        # The members themselves stay reachable, one level further in, so
        # grouping never costs the per-program actions.
        entries.extend(self._program_item(p) for p in members)
        return pystray.MenuItem(mark + group.name, pystray.Menu(*entries))

    def _program_item(self, program: Program) -> pystray.MenuItem:
        running = self.procs.is_running(program)
        entries = []
        if running:
            entries.append(pystray.MenuItem("Stop", self._make_stop(program)))
            entries.append(pystray.MenuItem("Restart", self._make_restart(program)))
        else:
            entries.append(pystray.MenuItem("Launch", self._make_launch(program)))
        if program.dashboard_url():
            # Programs that serve their own web UI get a direct open shortcut.
            entries.append(pystray.MenuItem("Open Dashboard",
                                            self._make_open_url(program)))
        entries.append(pystray.MenuItem("View Log", self._make_view_log(program)))
        entries.append(pystray.Menu.SEPARATOR)
        kind_label = "Python" if program.kind == KIND_PYTHON else "Shell"
        entries.append(pystray.MenuItem(f"[{kind_label}] {program.target}",
                                        None, enabled=False))
        summary = self.scheduler.summary(program)
        if summary:
            entries.append(pystray.MenuItem(f"⏰ Next: {summary}", None, enabled=False))
        mark = MARK_RUNNING if running else MARK_STOPPED
        return pystray.MenuItem(mark + program.name, pystray.Menu(*entries))

    # ---------- callbacks ----------
    # Nothing rebuilds the menu after an action: it is regenerated on the next
    # open, by which time the process state has settled.

    def _notify(self, title: str, message: str):
        """Report a failure as a tray balloon — the tray's equivalent of the
        alert dialog the macOS backend puts up."""
        try:
            self.icon.notify(message, title)
        except Exception:
            # Notifications are a courtesy; a Windows build that can't show
            # one must not turn a failed launch into a crashed navigator.
            pass

    def _make_launch(self, program: Program):
        def cb(icon, item):
            err = self.procs.launch(program)
            if err:
                self._notify(f"Could not launch “{program.name}”", err)
        return cb

    def _make_stop(self, program: Program):
        def cb(icon, item):
            err = self.procs.stop(program)
            if err and err != "Not running.":
                self._notify(f"Could not stop “{program.name}”", err)
        return cb

    def _make_restart(self, program: Program):
        def cb(icon, item):
            err = self.procs.restart(program)
            if err:
                self._notify(f"Could not restart “{program.name}”", err)
        return cb

    def _make_group_action(self, group: Group, action: str):
        def cb(icon, item):
            errors = self.procs.act_many(action, self.config.group_programs(group.id))
            if errors:
                # One notification for the group: a failing member shouldn't
                # put up a balloon per program in front of the ones that worked.
                self._notify(f"Could not {action} all of “{group.name}”",
                             "\n".join(errors))
        return cb

    def _make_open_url(self, program: Program):
        def cb(icon, item):
            # The program's own dashboard, opened straight in the default
            # browser — no need to go through the navigator dashboard.
            webbrowser.open(program.dashboard_url())
        return cb

    def _make_view_log(self, program: Program):
        def cb(icon, item):
            # Live-updating log page in the browser (auto-tails).
            self.dashboard.ensure_started()
            webbrowser.open(self.dashboard.url + f"log?id={program.id}")
        return cb

    def _on_dashboard(self, icon, item):
        self.dashboard.ensure_started()
        webbrowser.open(self.dashboard.url)

    def _on_toggle_login(self, icon, item):
        if login_item.is_enabled():
            login_item.disable()
        else:
            login_item.enable()

    def _on_restart_self(self, icon, item):
        """Relaunch the tray app itself, leaving launched programs running.

        A short-lived helper interpreter does the relaunching: it outlives this
        process, waits for the tray icon and the dashboard port to be released,
        and only then starts the replacement. Running programs are re-adopted
        from running.json on startup.
        """
        script = os.path.abspath(sys.argv[0])
        helper = (
            "import subprocess, sys, time; time.sleep(1.5); "
            "subprocess.Popen([sys.argv[1], sys.argv[2]], cwd=sys.argv[3], "
            "creationflags=subprocess.DETACHED_PROCESS)"
        )
        try:
            subprocess.Popen(
                [sys.executable, "-c", helper,
                 sys.executable, script, os.path.dirname(script) or "."],
                creationflags=subprocess.DETACHED_PROCESS,
            )
        except OSError as e:
            self._notify("Could not restart Mini App Navigator", str(e))
            return
        self.scheduler.stop()
        self.dashboard.stop()
        icon.stop()

    def _on_quit(self, icon, item):
        # Leave launched programs running so they can be re-adopted next time.
        self.scheduler.stop()
        self.dashboard.stop()
        icon.stop()

    def run(self):
        self.icon.run()


if __name__ == "__main__":
    MiniAppNavigator().run()
