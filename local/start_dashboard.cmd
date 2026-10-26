@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_p1_6_dashboard.ps1"
if errorlevel 1 (
  echo.
  echo Dashboard startup failed. No credentials were displayed or stored.
  pause
  exit /b 1
)
endlocal
