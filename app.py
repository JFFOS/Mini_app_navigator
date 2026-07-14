"""Mini App Navigator — a macOS menu bar app to launch and manage mini programs.

Each registered program (a Python script or a shell command) gets a submenu
with Launch / Stop / View Log actions. A running program is marked with a
checkmark. Programs can be added and removed from within the menu; the list is
persisted to ~/.mini_app_navigator/config.json.
"""

import subprocess

import rumps

from config import Config, Program, KIND_PYTHON, KIND_SHELL
from process_manager import ProcessManager

REFRESH_SECONDS = 3


class MiniAppNavigator(rumps.App):
    def __init__(self):
        super().__init__("Mini Apps", title="🚀", quit_button=None)
        self.config = Config()
        self.procs = ProcessManager()
        self._build_menu()
        # Periodically refresh running indicators.
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
        items.append(rumps.MenuItem("Add Mini Program…", callback=self.on_add))
        items.append(self._remove_menu())
        items.append(rumps.separator)
        items.append(rumps.MenuItem("Refresh", callback=lambda _: self._refresh(None)))
        items.append(rumps.MenuItem("Quit", callback=self.on_quit))
        self.menu = items

    def _program_menu(self, program: Program) -> rumps.MenuItem:
        running = self.procs.is_running(program.id)
        parent = rumps.MenuItem(program.name)
        parent.state = 1 if running else 0  # checkmark when running

        if running:
            parent.add(rumps.MenuItem(
                "Stop", callback=self._make_stop(program)))
        else:
            parent.add(rumps.MenuItem(
                "Launch", callback=self._make_launch(program)))
        parent.add(rumps.MenuItem("View Log", callback=self._make_view_log(program)))
        parent.add(rumps.separator)
        kind_label = "Python" if program.kind == KIND_PYTHON else "Shell"
        detail = rumps.MenuItem(f"[{kind_label}] {program.target}")
        detail.set_callback(None)
        parent.add(detail)
        return parent

    def _remove_menu(self) -> rumps.MenuItem:
        remove = rumps.MenuItem("Remove Mini Program")
        if not self.config.programs:
            remove.set_callback(None)
            return remove
        for program in self.config.programs:
            remove.add(rumps.MenuItem(program.name, callback=self._make_remove(program)))
        return remove

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
            path = program.log_path()
            # Open with the default viewer (Console/TextEdit).
            subprocess.Popen(["open", path])
        return cb

    def _make_remove(self, program: Program):
        def cb(_):
            if rumps.alert(
                title=f"Remove “{program.name}”?",
                message="This stops it if running and removes it from the menu.",
                ok="Remove", cancel="Cancel",
            ) == 1:
                self.procs.stop(program.id)
                self.config.remove(program.id)
                self._build_menu()
        return cb

    def on_add(self, _):
        program = self._prompt_program()
        if program is not None:
            self.config.add(program)
            self._build_menu()

    def on_quit(self, _):
        self.procs.stop_all()
        rumps.quit_application()

    # ---------- add dialog ----------

    def _prompt_program(self):
        # Name
        name = self._ask("Name", "A label for this mini program:")
        if not name:
            return None

        # Kind
        kind_answer = rumps.Window(
            message="Type 'py' for a Python script, or 'sh' for a shell command / app.",
            title="Kind", default_text="sh", ok="Next", cancel="Cancel",
            dimensions=(200, 20),
        ).run()
        if not kind_answer.clicked:
            return None
        kind = KIND_PYTHON if kind_answer.text.strip().lower().startswith("py") else KIND_SHELL

        # Target
        if kind == KIND_PYTHON:
            target = self._ask("Script path", "Absolute path to the .py file:")
        else:
            target = self._ask(
                "Command",
                "Shell command to run, e.g.  open -a Calculator  or  /path/to/tool",
            )
        if not target:
            return None

        args = self._ask("Arguments", "Extra arguments (optional):", required=False) or ""
        cwd = self._ask("Working dir", "Working directory (optional):", required=False) or ""

        return Program(name=name.strip(), kind=kind, target=target.strip(),
                       args=args.strip(), cwd=cwd.strip())

    @staticmethod
    def _ask(title, message, required=True):
        win = rumps.Window(message=message, title=title, ok="Next", cancel="Cancel",
                           dimensions=(320, 22))
        resp = win.run()
        if not resp.clicked:
            return None
        text = resp.text.strip()
        if required and not text:
            return None
        return text

    # ---------- periodic refresh ----------

    def _refresh(self, _):
        # Rebuild so checkmarks / Launch-vs-Stop reflect current process state.
        self._build_menu()


if __name__ == "__main__":
    MiniAppNavigator().run()
