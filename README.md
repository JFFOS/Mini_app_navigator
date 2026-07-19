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
- 🌐 Local web dashboard (stdlib only) to add/remove/manage programs
- 🔁 Launch-at-login toggle
- 💤 On-demand loading — the dashboard web server starts only when opened,
  keeping idle RAM low
- 🧷 Programs keep running when you quit the app, and are re-adopted on relaunch

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
