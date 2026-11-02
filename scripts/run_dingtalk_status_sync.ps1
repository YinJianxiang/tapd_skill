[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$PythonExecutable = "python"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$configPath = Join-Path $projectRoot ".tapd\dingtalk_status_sync.ps1"
$reportPath = Join-Path $projectRoot ".tapd\dingtalk_status_sync_last_result.json"
$previousLocation = Get-Location
$exitCode = 1
$startedAt = [DateTimeOffset]::Now.ToString("o")

try {
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
        throw "Missing local config: $configPath"
    }
    Set-Location -LiteralPath $projectRoot
    . $configPath
    $env:PYTHONIOENCODING = "utf-8"
    $arguments = @((Join-Path $PSScriptRoot "sync_dingtalk_status.py"))
    if ($Apply) { $arguments += "--apply" }
    $lines = & $PythonExecutable @arguments
    $exitCode = $LASTEXITCODE
    $result = ($lines -join "`n") | ConvertFrom-Json
    if ($null -eq $result -or $result.ok -isnot [bool]) {
        throw "Sync did not return a valid result"
    }
    if (-not $result.ok) { $exitCode = 1 }
    $report = [ordered]@{
        started_at = $startedAt
        finished_at = [DateTimeOffset]::Now.ToString("o")
        exit_code = $exitCode
        result = $result
    }
} catch {
    $exitCode = 1
    $report = [ordered]@{
        started_at = $startedAt
        finished_at = [DateTimeOffset]::Now.ToString("o")
        exit_code = 1
        result = @{ ok = $false; message = "Sync launcher failed; check local configuration, Python and DWS availability." }
    }
} finally {
    Set-Location -LiteralPath $previousLocation.Path
}

$json = $report | ConvertTo-Json -Depth 30
[System.IO.File]::WriteAllText($reportPath, $json, [System.Text.UTF8Encoding]::new($false))
Write-Output $json
exit $exitCode
