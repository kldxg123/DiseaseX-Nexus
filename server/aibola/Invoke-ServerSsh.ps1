param(
    [Parameter(Position = 0)]
    [ValidateSet("detect", "probe", "bootstrap", "sync", "verify_aibola", "check_env", "install_requirements", "prepare_data", "rebuild_labels", "data_status", "validate", "download_qwen", "train_lora", "start_api", "api_status")]
    [string]$Action = "probe",

    [switch]$IncludeData,

    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectRoot

$Python = "python"
$Module = switch ($Action) {
    "detect" { "src.remote.detect_server" }
    "probe" { "src.remote.probe" }
    "bootstrap" { "src.remote.bootstrap" }
    "sync" { "src.remote.sync" }
    "verify_aibola" { "src.remote.verify_aibola" }
    "check_env" { "src.remote.check_env" }
    "install_requirements" { "src.remote.install_requirements" }
    "prepare_data" { "src.remote.prepare_data" }
    "rebuild_labels" { "src.remote.rebuild_labels" }
    "data_status" { "src.remote.data_status" }
    "validate" { "src.remote.validate_remote" }
    "download_qwen" { "src.remote.download_qwen" }
    "train_lora" { "src.remote.train_lora" }
    "start_api" { "src.remote.start_api" }
    "api_status" { "src.remote.api_status" }
}

$ArgsList = @("-m", $Module)
if ($DryRun) {
    $ArgsList += "--dry-run"
}
if ($Action -eq "sync" -and $IncludeData) {
    $ArgsList += "--include-data"
}

& $Python @ArgsList
exit $LASTEXITCODE
