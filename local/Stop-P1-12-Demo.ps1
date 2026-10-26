param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$pidFile = Join-Path $PSScriptRoot ".runtime\a100_project_tunnel.pid"
$bundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$python = if (Test-Path -LiteralPath $bundledPython) {
    $bundledPython
}
else {
    (Get-Command python -ErrorAction Stop).Source
}

if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Host "No launcher-managed tunnel is recorded. Remote services were not changed."
    exit 0
}

$savedPid = 0
$savedPidText = (Get-Content -LiteralPath $pidFile -Raw).Trim()
if (-not [int]::TryParse($savedPidText, [ref]$savedPid)) {
    throw "The launcher PID record is invalid; no process was stopped."
}
$candidate = Get-Process -Id $savedPid -ErrorAction SilentlyContinue
if ($null -eq $candidate) {
    Remove-Item -LiteralPath $pidFile -Force
    Write-Host "The recorded tunnel was already stopped. Remote services were not changed."
    exit 0
}
if ($candidate.Path -ne $python) {
    throw "The recorded PID does not belong to this project's tunnel helper; no process was stopped."
}

Stop-Process -Id $savedPid
Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
Write-Host "Local ports 8120/8121 were closed. Remote services and daily updates remain running."
