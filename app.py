"""Platform dispatcher for the Mini App Navigator tray / menu bar app.

Picks the macOS backend (rumps, app_mac.py) or the Windows backend
(pystray, app_win.py) based on the running platform. Both expose the same
MiniAppNavigator class, so mini_app_navigator.py doesn't care which is in use.
"""

import sys

if sys.platform == "darwin":
    from app_mac import MiniAppNavigator
elif sys.platform == "win32":
    from app_win import MiniAppNavigator
else:
    raise RuntimeError(
        f"Mini App Navigator supports macOS and Windows; not: {sys.platform}")

__all__ = ["MiniAppNavigator"]
