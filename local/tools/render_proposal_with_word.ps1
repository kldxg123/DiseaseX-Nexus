param([Parameter(Mandatory=$true)][string]$InputDocx, [Parameter(Mandatory=$true)][string]$OutputPdf)
$ErrorActionPreference = 'Stop'
$wordApp = $null
$wordDocument = $null
try {
    $wordApp = New-Object -ComObject Word.Application
    $wordApp.Visible = $false
    $wordApp.DisplayAlerts = 0
    $wordDocument = $wordApp.Documents.Open($InputDocx, $false, $true)
    $wordDocument.Repaginate()
    $wordDocument.ExportAsFixedFormat($OutputPdf, 17)
    Write-Output ('PDF_CREATED=' + (Test-Path -LiteralPath $OutputPdf))
} finally {
    if ($null -ne $wordDocument) { try { $wordDocument.Close(0) } catch {} }
    if ($null -ne $wordApp) { try { $wordApp.Quit() } catch {} }
}
