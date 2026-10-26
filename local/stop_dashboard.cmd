@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_p1_6_dashboard.ps1"
if errorlevel 1 (
  echo.
  echo Dashboard tunnel shutdown failed.
  pause
  exit /b 1
)
endlocal
