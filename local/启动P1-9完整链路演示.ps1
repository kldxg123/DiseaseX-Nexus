[CmdletBinding()]
param(
    [switch]$ResetDemo,
    [switch]$NoBrowser
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$workspace = $PSScriptRoot
$helper = Join-Path $workspace 'tools\a100_p1_9.py'
$tunnelHelper = Join-Path $workspace 'tools\hold_a100_p1_9_tunnel.py'
$pidFile = Join-Path $workspace 'p1_9_tunnel.pid'
$python = (Get-Command python -ErrorAction Stop).Source

Push-Location $workspace
try {
    & $python $helper formal-immutability-check
    if ($LASTEXITCODE -ne 0) { throw 'Formal immutability or service health check failed.' }

    if ($ResetDemo) {
        & $python $helper reset-demo
    } else {
        & $python $helper ensure-demo
    }
    if ($LASTEXITCODE -ne 0) { throw 'P1-9 demo startup failed.' }

    $tunnelAlive = $false
    if (Test-Path -LiteralPath $pidFile) {
        $storedPid = 0
        if ([int]::TryParse((Get-Content -LiteralPath $pidFile -Raw).Trim(), [ref]$storedPid)) {
            $process = Get-CimInstance Win32_Process -Filter "ProcessId=$storedPid" -ErrorAction SilentlyContinue
            $tunnelAlive = $null -ne $process -and $process.CommandLine -like "*$tunnelHelper*"
        }
        if (-not $tunnelAlive) { Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue }
    }
    if (-not $tunnelAlive) {
        Start-Process -FilePath $python -ArgumentList @($tunnelHelper) -WorkingDirectory $workspace -WindowStyle Hidden | Out-Null
    }

    $ready = $false
    foreach ($attempt in 1..40) {
        try {
            $dashboard = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8122/healthz' -TimeoutSec 2
            $review = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8123/healthz' -TimeoutSec 2
            if ($dashboard.StatusCode -eq 200 -and $review.StatusCode -eq 200) { $ready = $true; break }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $ready) { throw 'Local ports 8122 and 8123 did not become ready.' }

    & $python $helper copy-demo-username
    if ($LASTEXITCODE -ne 0) { throw 'Could not copy the demo username.' }
    if (-not $NoBrowser) {
        Start-Process 'http://127.0.0.1:8123/p1-9/reviews'
        Start-Process 'http://127.0.0.1:8122/'
    }

    Write-Host 'P1-9 demo is running. It cannot write to the formal database.'
    Write-Host 'The demo username is on the clipboard. Run the password-copy helper when needed.'
    if ($ResetDemo) { Write-Host 'The demo was safely reset and is ready for a new Discovery run.' }
} finally {
    Pop-Location
}
