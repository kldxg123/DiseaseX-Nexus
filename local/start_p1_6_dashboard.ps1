$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$dashboardUrl = "http://127.0.0.1:8020"
$healthUrl = "$dashboardUrl/healthz"
$reviewUrl = "http://127.0.0.1:8021"
$reviewHealthUrl = "$reviewUrl/healthz"
$remoteStarter = Join-Path $root "tools\autodl_p1_6.py"
$tunnelHelper = Join-Path $root "tools\hold_p1_6_tunnel.py"
$pidFile = Join-Path $root "private\p1_6_dashboard_tunnel.pid"

function Test-DashboardHealth {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $healthUrl -TimeoutSec 2
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

function Test-ReviewHealth {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $reviewHealthUrl -TimeoutSec 2
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
    $savedPid = [int](Get-Content -LiteralPath $pidFile -Raw)
    $candidate = Get-CimInstance Win32_Process -Filter "ProcessId = $savedPid" -ErrorAction SilentlyContinue
    if ($null -ne $candidate -and $candidate.CommandLine -like "*hold_p1_6_tunnel.py*") {
        Stop-Process -Id $savedPid -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}

if ((Test-DashboardHealth) -and (Test-ReviewHealth)) {
    Write-Host "Dashboard and Source Review Console are already available locally."
    Start-Process $dashboardUrl
    Start-Process $reviewUrl
    exit 0
}

foreach ($requiredFile in @($remoteStarter, $tunnelHelper)) {
    if (-not (Test-Path -LiteralPath $requiredFile)) {
        throw "Required local helper is missing."
    }
}

$python = (Get-Command python -ErrorAction Stop).Source
Stop-StaleTunnel

Write-Host "Starting the remote Dashboard service..."
& $python $remoteStarter dashboard-start *> $null
if ($LASTEXITCODE -ne 0) {
    throw "The remote Dashboard failed to start. No credentials were displayed or stored."
}

& $python (Join-Path $root "tools\autodl_p1_7a.py") services-start *> $null
if ($LASTEXITCODE -ne 0) {
    throw "The remote Source Review services failed to start."
}

Write-Host "Creating the local-only SSH tunnel..."
$tunnelProcess = Start-Process -FilePath $python `
    -ArgumentList @($tunnelHelper, "--daemon") `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -PassThru

$healthy = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    if ((Test-DashboardHealth) -and (Test-ReviewHealth)) {
        $healthy = $true
        break
    }
    if ($tunnelProcess.HasExited) {
        break
    }
    Start-Sleep -Seconds 1
}

if (-not $healthy) {
    Stop-StaleTunnel
    throw "The local Dashboard tunnel did not become healthy."
}

Write-Host "Dashboard: $dashboardUrl"
Write-Host "Source Review Console: $reviewUrl"
Start-Process $dashboardUrl
Start-Process $reviewUrl
