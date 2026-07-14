"""Mini App Navigator — a macOS menu bar app to launch and manage mini programs.

Each registered program (a Python script or a shell command) gets a submenu
with Launch / Stop / View Log actions, and a checkmark while running. Adding,
removing, and full management happen in a local web dashboard (see dashboard.py)
opened from the menu. The program list is persisted to
~/.mini_app_navigator/config.json.
"""

import subprocess
import webbrowser

import rumps

from config import Config, Program, KIND_PYTHON
from process_manager import ProcessManager
from dashboard import DashboardServer

REFRESH_SECONDS = 3


class MiniAppNavigator(rumps.App):
    def __init__(self):
        super().__init__("Mini Apps", title="🚀", quit_button=None)
        self.config = Config()
        self.procs = ProcessManager()
        # The dashboard shares these exact instances, so menu and web UI stay
        # in sync automatically.
        self.dashboard = DashboardServer(self.config, self.procs)
        self.dashboard.start()
        self._build_menu()
        # Periodically refresh running indicators + pick up dashboard changes.
        self._timer = rumps.Timer(self._refresh, REFRESH_SECONDS)
        self._timer.start()

    # ---------- menu construction ----------

    def _build_menu(self):
        self.menu.clear()
        items = []
        if not self.config.programs:
            placeholder = rumps.MenuItem("No mini programs yet")
            placeholder.set_callback(None)
            items.append(placeholder)
        else:
            for program in self.config.programs:
                items.append(self._program_menu(program))

        items.append(rumps.separator)
        items.append(rumps.MenuItem("Open Dashboard…", callback=self.on_dashboard))
        items.append(rumps.separator)
        items.append(rumps.MenuItem("Refresh", callback=lambda _: self._refresh(None)))
        items.append(rumps.MenuItem("Quit", callback=self.on_quit))
        self.menu = items

    def _program_menu(self, program: Program) -> rumps.MenuItem:
        running = self.procs.is_running(program.id)
        parent = rumps.MenuItem(program.name)
        parent.state = 1 if running else 0  # checkmark when running

        if running:
            parent.add(rumps.MenuItem("Stop", callback=self._make_stop(program)))
        else:
            parent.add(rumps.MenuItem("Launch", callback=self._make_launch(program)))
        parent.add(rumps.MenuItem("View Log", callback=self._make_view_log(program)))
        parent.add(rumps.separator)
        kind_label = "Python" if program.kind == KIND_PYTHON else "Shell"
        detail = rumps.MenuItem(f"[{kind_label}] {program.target}")
        detail.set_callback(None)
        parent.add(detail)
        return parent

    # ---------- callbacks ----------

    def _make_launch(self, program: Program):
        def cb(_):
            err = self.procs.launch(program)
            if err:
                rumps.alert(f"Could not launch “{program.name}”", err)
            self._build_menu()
        return cb

    def _make_stop(self, program: Program):
        def cb(_):
            err = self.procs.stop(program.id)
            if err and err != "Not running.":
                rumps.alert(f"Could not stop “{program.name}”", err)
            self._build_menu()
        return cb

    def _make_view_log(self, program: Program):
        def cb(_):
            subprocess.Popen(["open", program.log_path()])
        return cb

    def on_dashboard(self, _):
        webbrowser.open(self.dashboard.url)

    def on_quit(self, _):
        self.procs.stop_all()
        self.dashboard.stop()
        rumps.quit_application()

    # ---------- periodic refresh ----------

    def _refresh(self, _):
        # Rebuild so checkmarks / Launch-vs-Stop and any dashboard-side
        # add/remove are reflected in the menu.
        self._build_menu()


if __name__ == "__main__":
    MiniAppNavigator().run()
