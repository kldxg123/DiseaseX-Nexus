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
$previewGateHelper = Join-Path $root "tools\a100_p1_12.py"
$serverInfoMatches = @(Get-ChildItem -LiteralPath $root -File | Where-Object { $_.Name -like "*a100.txt" })
if ($serverInfoMatches.Count -ne 1) {
    throw "Expected exactly one A100 server information file."
}
$serverInfo = $serverInfoMatches[0].FullName
$pidDirectory = Join-Path $root ".runtime"
$pidFile = Join-Path $pidDirectory "a100_project_tunnel.pid"
$knownHostsFile = Join-Path $pidDirectory "known_hosts_a100_epidemic"
$bundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

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
    $savedPid = [int](Get-Content -LiteralPath $pidFile -Raw)
    $candidate = Get-Process -Id $savedPid -ErrorAction SilentlyContinue
    if ($null -ne $candidate -and $candidate.Path -eq $python) {
        Stop-Process -Id $savedPid -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}

foreach ($requiredFile in @($migrationHelper, $tunnelHelper, $previewGateHelper, $serverInfo)) {
    if (-not (Test-Path -LiteralPath $requiredFile)) {
        throw "Required local file is missing."
    }
}

$python = if (Test-Path -LiteralPath $bundledPython) {
    $bundledPython
}
else {
    (Get-Command python -ErrorAction Stop).Source
}

New-Item -ItemType Directory -Path $pidDirectory -Force | Out-Null

Write-Host "Starting and checking A100 project services..."
& $python $migrationHelper start-target-services *> $null
if ($LASTEXITCODE -ne 0) {
    throw "A100 project services failed to start. No credentials were displayed or stored."
}

Write-Host "Validating production deployment, Git revision, and database marker..."
& $python $previewGateHelper launcher-gate *> $null
if ($LASTEXITCODE -ne 0) {
    throw "A100 production deployment identity gate failed."
}

Write-Host "Running deterministic startup catch-up..."
$reconcileOutput = & $python $migrationHelper reconcile-target-daily-startup 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "A100 startup catch-up failed. No credentials were displayed or stored."
}
$reconcile = ($reconcileOutput -join [Environment]::NewLine) | ConvertFrom-Json
if ($reconcile.startup_reconcile.status -ne "PASS") {
    throw "A100 startup catch-up returned an invalid status."
}
Write-Host ("Startup catch-up: created={0}; deduplicated={1}; runs={2}" -f `
        $reconcile.startup_reconcile.created, `
        $reconcile.startup_reconcile.deduplicated, `
        $reconcile.startup_reconcile.run_count)

# The local ports alone cannot prove which rented instance owns an existing
# tunnel. Recreate only this launcher's recorded tunnel on every run so a
# healthy tunnel from an older instance is never silently reused.
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

Write-Host "Dashboard: $dashboardUrl"
Write-Host "Source Review Console: $reviewUrl"
if (-not $NoBrowser) {
    Start-Process $dashboardUrl
    Start-Process $reviewUrl
}
