@echo off
rem Double-click this file to stop the Mini App Navigator tray app.
rem Note: this stops the tray app only. Programs you launched keep running
rem (that's by design) and are re-adopted next time you start the app.

rem Match only this app's unique entry point, not any other project's script.
rem $PID excludes this very PowerShell: the pattern is in its own command line,
rem so without it the script would find -- and kill -- itself.
powershell -NoProfile -NonInteractive -Command ^
  "$me = $PID;" ^
  "$p = Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $me -and $_.CommandLine -like '*mini_app_navigator.py*' };" ^
  "if ($p) { $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force };" ^
  "Write-Host 'Mini App Navigator stopped.' } else { Write-Host 'Mini App Navigator is not running.' }"

timeout /t 2 /nobreak >nul
exit
