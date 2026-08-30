"""Manage the 'Launch at Login' setting via a macOS LaunchAgent.

Enabling writes ~/Library/LaunchAgents/com.mini-app-navigator.plist and loads
it so launchd starts the app at login. Disabling unloads and removes it.

Selected automatically by login_item.py on macOS; see login_item_win.py for
the Windows (registry Run key) equivalent.
"""

import os
import plistlib
import subprocess
import sys

from config import LOG_DIR

LABEL = "com.mini-app-navigator"
PLIST_PATH = os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")


def _script_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "mini_app_navigator.py")


def is_enabled() -> bool:
    return os.path.exists(PLIST_PATH)


def enable() -> None:
    project_dir = os.path.dirname(os.path.abspath(__file__))
    plist = {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, _script_path()],
        "WorkingDirectory": project_dir,
        "RunAtLoad": True,
        "KeepAlive": False,
        "StandardOutPath": os.path.join(LOG_DIR, "launch-agent.log"),
        "StandardErrorPath": os.path.join(LOG_DIR, "launch-agent.log"),
    }
    os.makedirs(os.path.dirname(PLIST_PATH), exist_ok=True)
    with open(PLIST_PATH, "wb") as f:
        plistlib.dump(plist, f)
    # Reload so the change takes effect immediately (ignore "not loaded" noise).
    subprocess.run(["launchctl", "unload", PLIST_PATH], capture_output=True)
    subprocess.run(["launchctl", "load", PLIST_PATH], capture_output=True)


def disable() -> None:
    subprocess.run(["launchctl", "unload", PLIST_PATH], capture_output=True)
    try:
        os.remove(PLIST_PATH)
    except OSError:
        pass
