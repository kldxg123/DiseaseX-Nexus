param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$stopper = Join-Path $PSScriptRoot "Stop-P1-12-Demo.ps1"
if (-not (Test-Path -LiteralPath $stopper)) {
    throw "The shared A100 tunnel stopper is missing."
}

& $stopper
if ($LASTEXITCODE -ne 0) {
    throw "The local P1-12 tunnel did not stop cleanly."
}
