@echo off
rem Double-click this file to restart the Mini App Navigator tray app.
rem Stops the running instance (if any) and starts a fresh one.
rem Note: programs you launched keep running (that's by design) and are
rem re-adopted by the new instance.

cd /d "%~dp0"

rem Match only this app's unique entry point, not any other project's script.
rem Waits for the old instance to go so it has released its tray icon and its
rem dashboard port before the new one starts. $PID excludes this very
rem PowerShell: the pattern is in its own command line, so without it the
rem script would find -- and kill -- itself.
powershell -NoProfile -NonInteractive -Command ^
  "$me = $PID;" ^
  "$f = { Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $me -and $_.CommandLine -like '*mini_app_navigator.py*' } };" ^
  "& $f | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue };" ^
  "for ($i = 0; $i -lt 10 -and (& $f); $i++) { Start-Sleep -Milliseconds 500 }"

rem Read the interpreter from the local, gitignored config if present
rem (copy launcher.local.bat.example -> launcher.local.bat). Fall back to
rem whatever pythonw.exe is on PATH.
set "PYTHONW="
if exist "launcher.local.bat" call "launcher.local.bat"
if defined PYTHONW if not exist "%PYTHONW%" set "PYTHONW="
if not defined PYTHONW set "PYTHONW=pythonw.exe"

start "" "%PYTHONW%" mini_app_navigator.py
exit
