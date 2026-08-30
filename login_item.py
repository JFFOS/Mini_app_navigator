"""Platform dispatcher for the 'Launch at Login' setting.

Picks the macOS backend (a LaunchAgent plist, login_item_mac.py) or the
Windows backend (a registry Run key, login_item_win.py) based on the running
platform. Both expose is_enabled / enable / disable.

On an unsupported platform the setting reads as off and can't be turned on,
so a caller can still offer the menu entry without special-casing anything.
"""

import sys

if sys.platform == "darwin":
    from login_item_mac import is_enabled, enable, disable
elif sys.platform == "win32":
    from login_item_win import is_enabled, enable, disable
else:
    def is_enabled() -> bool:
        return False

    def enable() -> None:
        raise RuntimeError(
            f"Launch at Login is not supported on platform: {sys.platform}")

    def disable() -> None:
        pass

__all__ = ["is_enabled", "enable", "disable"]
