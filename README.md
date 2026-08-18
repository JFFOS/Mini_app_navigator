# Mini App Navigator

A lightweight macOS **menu bar app** to launch and manage your "mini programs" —
small Python scripts or shell commands — from one place.

Each registered program gets a submenu with **Launch / Stop / View Log** actions
and a checkmark while it's running. Adding, removing, and full management happen
in a local web dashboard opened from the menu.

## Features

- 🚀 Menu bar launcher for Python scripts and shell commands
- ▶️ Launch / stop programs and see live running status
- 📄 Per-program log files with a live-tailing web log viewer
- 🔗 Optional dashboard URL per program — an **Open** button (menu bar and web
  dashboard) opens the program's own web UI in your default browser
- 🩺 Optional **liveness probe** per program, for commands that only *launch*
  something and exit — see below
- ✏️ **Edit** button on every entry — change name, kind, command/script,
  arguments, working dir, dashboard URL and probe in place
- ⚡️ Per-program **Auto launch** toggle (off by default) — flagged programs
  start with the navigator
- 🌐 Local web dashboard (stdlib only) to add/remove/manage programs
- 🔁 Launch-at-login toggle
- 💤 On-demand loading — the dashboard web server starts only when opened,
  keeping idle RAM low
- 🧷 Programs keep running when you quit the app, and are re-adopted on relaunch

## Liveness probes

Status is normally tracked by the PID of the command that was launched. That
breaks for a target that is only a *launcher* — `open -a Stats.app`, or a script
ending in `nohup … &` — because it hands off to a detached process and exits
immediately. The navigator would show **stopped** while the program is running,
re-launch it on every start, and have nothing to Stop.

Give those entries a **liveness probe** and the navigator will find the real
process, adopt it, and manage it normally:

| Probe | Finds the program by |
|---|---|
| *(empty)* | the port of the program's dashboard URL, if that URL is a local one |
| `8787` (or `port:8787`) | a process listening on that local port |
| `match:shortcutguard_app.py` | `pgrep -f` against the full command line |

So a program with a `http://127.0.0.1:8787/` dashboard URL needs no probe at
all — the URL is the probe. Fill the field in only to override that, or for a
program with no local web UI.

Adopted processes are shown with a **via probe** badge in the dashboard, and are
stopped on their own (not as a process group), since the navigator didn't create
that group.

## Requirements

- macOS
- Python 3.8+
- [`rumps`](https://pypi.org/project/rumps/)

## Install

```bash
pip install -r requirements.txt
```

## Run

```bash
python mini_app_navigator.py
```

Or use the included **Start**, **Stop**, and **Restart Mini App
Navigator.command** launchers.

Configuration and logs live under `~/.mini_app_navigator/`.

## License

[MIT](LICENSE)
