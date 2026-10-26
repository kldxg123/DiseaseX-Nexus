param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = $PSScriptRoot
$dashboardUrl = "http://127.0.0.1:8120"
$reviewUrl = "http://127.0.0.1:8121"
$migrationHelper = Join-Path $root "tools\migrate_epidemic_to_a100.py"
$tunnelHelper = Join-Path $root "tools\hold_a100_project_tunnel.py"
$gateHelper = Join-Path $root "tools\a100_p1_12.py"
$serverInfoMatches = @(Get-ChildItem -LiteralPath $root -File | Where-Object { $_.Name -like "*a100.txt" })
if ($serverInfoMatches.Count -ne 1) {
    throw "Expected exactly one A100 server information file."
}
$pidDirectory = Join-Path $root ".runtime"
$pidFile = Join-Path $pidDirectory "a100_project_tunnel.pid"
$knownHostsFile = Join-Path $pidDirectory "known_hosts_a100_epidemic"
$bundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$python = if (Test-Path -LiteralPath $bundledPython) {
    $bundledPython
}
else {
    (Get-Command python -ErrorAction Stop).Source
}

function Test-Ready([string]$Url) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

function Stop-StaleTunnel {
    if (-not (Test-Path -LiteralPath $pidFile)) {
        return
    }
    $savedPid = 0
    $savedPidText = (Get-Content -LiteralPath $pidFile -Raw).Trim()
    if (-not [int]::TryParse($savedPidText, [ref]$savedPid)) {
        throw "The launcher PID record is invalid."
    }
    $candidate = Get-Process -Id $savedPid -ErrorAction SilentlyContinue
    if ($null -ne $candidate -and $candidate.Path -eq $python) {
        Stop-Process -Id $savedPid -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}

foreach ($requiredFile in @($migrationHelper, $tunnelHelper, $gateHelper, $serverInfoMatches[0].FullName)) {
    if (-not (Test-Path -LiteralPath $requiredFile)) {
        throw "Required local file is missing."
    }
}

New-Item -ItemType Directory -Path $pidDirectory -Force | Out-Null

Write-Host "Starting and checking A100 project services..."
& $python $migrationHelper start-target-services *> $null
if ($LASTEXITCODE -ne 0) {
    throw "A100 project services failed to start."
}

Write-Host "Validating the P1-12 production deployment..."
& $python $gateHelper launcher-gate *> $null
if ($LASTEXITCODE -ne 0) {
    throw "The P1-12 deployment identity gate failed."
}

Write-Host "Running deterministic startup catch-up..."
$reconcileOutput = & $python $migrationHelper reconcile-target-daily-startup 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "A100 startup catch-up failed."
}
$reconcile = ($reconcileOutput -join [Environment]::NewLine) | ConvertFrom-Json
if ($reconcile.startup_reconcile.status -ne "PASS") {
    throw "A100 startup catch-up returned an invalid status."
}

Stop-StaleTunnel
Write-Host "Creating local-only SSH tunnels..."
$tunnelProcess = Start-Process -FilePath $python `
    -ArgumentList @("`"$tunnelHelper`"", "--pid-file", "`"$pidFile`"", "--known-hosts", "`"$knownHostsFile`"") `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -PassThru
Set-Content -LiteralPath $pidFile -Value $tunnelProcess.Id -Encoding Ascii

$healthy = $false
for ($attempt = 0; $attempt -lt 45; $attempt++) {
    if ($tunnelProcess.HasExited) {
        break
    }
    if ((Test-Ready "$dashboardUrl/readyz") -and (Test-Ready "$reviewUrl/readyz")) {
        $healthy = $true
        break
    }
    Start-Sleep -Seconds 1
}
if (-not $healthy) {
    Stop-StaleTunnel
    throw "The A100 local tunnels did not become healthy."
}

$dashboard = "$dashboardUrl/p1-12/trusted"
$reviews = "$reviewUrl/p1-12/reviews"
Write-Host "P1-12 trusted Dashboard: $dashboard"
Write-Host "P1-12 Candidate Review Console: $reviews"
if (-not $NoBrowser) {
    Start-Process $dashboard
    Start-Process $reviews
}
