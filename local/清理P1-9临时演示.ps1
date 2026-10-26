[CmdletBinding()]
param(
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$workspace = $PSScriptRoot
$helper = Join-Path $workspace 'tools\a100_p1_9.py'
$tunnelHelper = Join-Path $workspace 'tools\hold_a100_p1_9_tunnel.py'
$pidFile = Join-Path $workspace 'p1_9_tunnel.pid'
$python = (Get-Command python -ErrorAction Stop).Source

if (-not $Force) {
    $answer = Read-Host 'Type CLEANUP P1-9 DEMO to remove the exact marker-verified demo environment'
    if ($answer -cne 'CLEANUP P1-9 DEMO') { throw 'Cleanup cancelled.' }
}

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
    & $python $helper cleanup-demo
    if ($LASTEXITCODE -ne 0) { throw 'The exact path or marker gate failed; nothing was removed.' }
    Write-Host 'Marker-verified P1-9 demo database, storage, and credentials were removed.'
} finally {
    Pop-Location
}
