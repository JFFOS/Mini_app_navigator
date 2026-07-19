#!/bin/bash
# Double-click this file in Finder to restart the Mini App Navigator menu bar app.
# Stops the running instance (if any) and starts a fresh one.
# Note: programs you launched keep running (that's by design) and are re-adopted
# by the new instance.

cd "$(dirname "$0")" || exit 1

# Match only this app's unique entry point, not any other project's script.
if pgrep -f "mini_app_navigator.py" >/dev/null 2>&1; then
    pkill -f "mini_app_navigator.py"
    # Give the old instance a moment to release its menu bar item.
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        pgrep -f "mini_app_navigator.py" >/dev/null 2>&1 || break
        sleep 0.5
    done
fi

# Read the interpreter from the local, gitignored config if present
# (copy launcher.local.example -> launcher.local). Fall back to python3 on PATH.
PYTHON=""
[ -f "launcher.local" ] && . "./launcher.local"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"

# Start detached so the app keeps running after this terminal window closes.
nohup "$PYTHON" mini_app_navigator.py >/dev/null 2>&1 &

# Close the Terminal window that Finder opened for the .command.
osascript -e 'tell application "Terminal" to close (every window whose name contains "Restart Mini App Navigator.command")' >/dev/null 2>&1 &
exit 0
