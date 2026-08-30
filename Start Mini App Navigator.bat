@echo off
rem Double-click this file to start Mini App Navigator.
rem It launches the system-tray app (look for the icon near the clock) with no
rem console window of its own, then this window closes itself.

cd /d "%~dp0"

rem Read the interpreter from the local, gitignored config if present
rem (copy launcher.local.bat.example -> launcher.local.bat). Fall back to
rem whatever pythonw.exe is on PATH.
set "PYTHONW="
if exist "launcher.local.bat" call "launcher.local.bat"
if defined PYTHONW if not exist "%PYTHONW%" set "PYTHONW="
if not defined PYTHONW set "PYTHONW=pythonw.exe"

rem pythonw.exe has no console, so the app is detached from this window and
rem keeps running once it closes.
start "" "%PYTHONW%" mini_app_navigator.py
exit
