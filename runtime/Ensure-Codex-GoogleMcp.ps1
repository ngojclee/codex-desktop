# Google MCP bootstrap retired. Business MCP owns Google Workspace routing.
# Keep a no-op compatibility shim for callers from older installed updaters.

[CmdletBinding()]
param(
    [string]$CodexHome = (Join-Path $env:USERPROFILE '.codex'),
    [string]$Endpoint = $env:CODEX_GOOGLE_MCP_URL,
    [double]$StartupTimeoutSec = 45.0,
    [int]$BackupKeep = 5,
    [switch]$Quiet,
    [switch]$Json
)

$result = [ordered]@{
    changed = $false
    disabled = $true
    retired = $true
    config = (Join-Path $CodexHome 'config.toml')
}

if ($Json) {
    $result | ConvertTo-Json -Compress
} elseif (-not $Quiet) {
    Write-Host 'Google MCP bootstrap retired; existing MCP configuration left unchanged.'
}
