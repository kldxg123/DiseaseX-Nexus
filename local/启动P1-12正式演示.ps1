param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = $PSScriptRoot
$launcher = Join-Path $root "Start-P1-12-Demo.ps1"
if (-not (Test-Path -LiteralPath $launcher)) {
    throw "The shared A100 launcher is missing."
}

& $launcher -NoBrowser
if ($LASTEXITCODE -ne 0) {
    throw "The A100 project launcher did not complete successfully."
}

$dashboard = "http://127.0.0.1:8120/p1-12/trusted"
$reviews = "http://127.0.0.1:8121/p1-12/reviews"
Write-Host "P1-12 trusted Dashboard: $dashboard"
Write-Host "P1-12 Candidate Review Console: $reviews"

if (-not $NoBrowser) {
    Start-Process $dashboard
    Start-Process $reviews
}
