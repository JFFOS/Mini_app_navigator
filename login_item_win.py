"""Manage the 'Launch at Login' setting via the Windows registry.

Enabling writes a value under
HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run so Windows
starts the app at login. Disabling removes that value.

The per-user Run key is deliberate: it needs no administrator rights, and it
runs the app as the logged-in user, which is the only account whose desktop has
a system tray to put an icon in.

Selected automatically by login_item.py on Windows; see login_item_mac.py for
the macOS (LaunchAgent) equivalent.
"""

import os
import sys
import winreg

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "MiniAppNavigator"


def _script_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "mini_app_navigator.py")


def _launch_command() -> str:
    """The command Windows runs at login, as a quoted command line.

    Prefers pythonw.exe next to the current interpreter so the app starts
    without a console window flashing up (and staying) behind the tray icon.
    """
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pythonw if os.path.exists(pythonw) else sys.executable
    return f'"{exe}" "{_script_path()}"'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
        return True
    except FileNotFoundError:
        return False


def enable() -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _launch_command())


def disable() -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        pass
