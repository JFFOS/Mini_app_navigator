#!/bin/bash
# Double-click this file in Finder to start Mini App Navigator.
# It launches the menu bar app (look for the 🚀 icon), then closes this window.

cd "$(dirname "$0")" || exit 1

# Read the interpreter from the local, gitignored config if present
# (copy launcher.local.example -> launcher.local). Fall back to python3 on PATH.
PYTHON=""
[ -f "launcher.local" ] && . "./launcher.local"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"

# Start detached so the app keeps running after this terminal window closes.
nohup "$PYTHON" mini_app_navigator.py >/dev/null 2>&1 &

# Close the Terminal window that Finder opened for the .command.
osascript -e 'tell application "Terminal" to close (every window whose name contains "Start Mini App Navigator.command")' >/dev/null 2>&1 &
exit 0
