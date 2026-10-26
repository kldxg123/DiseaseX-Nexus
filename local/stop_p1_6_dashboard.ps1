$ErrorActionPreference = "Stop"
$pidFile = Join-Path $PSScriptRoot "private\p1_6_dashboard_tunnel.pid"

if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Host "Dashboard tunnel is not running."
    exit 0
}

$savedPid = [int](Get-Content -LiteralPath $pidFile -Raw)
$candidate = Get-CimInstance Win32_Process -Filter "ProcessId = $savedPid" -ErrorAction SilentlyContinue
if ($null -ne $candidate -and $candidate.CommandLine -like "*hold_p1_6_tunnel.py*") {
    Stop-Process -Id $savedPid
}
Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
Write-Host "Dashboard tunnel stopped."
