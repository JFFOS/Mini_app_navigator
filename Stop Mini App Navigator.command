#!/bin/bash
# Double-click this file in Finder to stop the Mini App Navigator menu bar app.
# Note: this stops the menu bar app only. Programs you launched keep running
# (that's by design) and are re-adopted next time you start the app.

# Match only this app's unique entry point, not any other project's script.
if pgrep -f "mini_app_navigator.py" >/dev/null 2>&1; then
    pkill -f "mini_app_navigator.py"
    echo "Mini App Navigator stopped."
else
    echo "Mini App Navigator is not running."
fi

# Close the Terminal window that Finder opened for the .command.
osascript -e 'tell application "Terminal" to close (every window whose name contains "Stop Mini App Navigator.command")' >/dev/null 2>&1 &
sleep 1
exit 0
