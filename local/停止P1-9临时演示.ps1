[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$workspace = $PSScriptRoot
$helper = Join-Path $workspace 'tools\a100_p1_9.py'
$tunnelHelper = Join-Path $workspace 'tools\hold_a100_p1_9_tunnel.py'
$pidFile = Join-Path $workspace 'p1_9_tunnel.pid'
$python = (Get-Command python -ErrorAction Stop).Source

Push-Location $workspace
try {
    & $python $helper stop-demo
    if ($LASTEXITCODE -ne 0) { throw 'Remote demo services did not stop cleanly.' }

    if (Test-Path -LiteralPath $pidFile) {
        $storedPid = 0
        if ([int]::TryParse((Get-Content -LiteralPath $pidFile -Raw).Trim(), [ref]$storedPid)) {
            $process = Get-CimInstance Win32_Process -Filter "ProcessId=$storedPid" -ErrorAction SilentlyContinue
            if ($null -ne $process -and $process.CommandLine -like "*$tunnelHelper*") {
                Stop-Process -Id $storedPid -Force
            }
        }
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    }
    $localLogin = Join-Path ([System.IO.Path]::GetTempPath()) 'epidemic_p1_9_demo_login_runtime.json'
    Remove-Item -LiteralPath $localLogin -Force -ErrorAction SilentlyContinue
    Write-Host 'P1-9 demo services and local tunnels stopped. Demo data and artifacts were retained.'
} finally {
    Pop-Location
}
