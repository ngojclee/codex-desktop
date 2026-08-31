#!/usr/bin/env pwsh
# Ensure-Codex-WslNative.ps1
#
# Keeps Codex Desktop on the native Windows agent path.
#
# Why: Desktop 26.820/26.825 injects a transport-less mcp_servers.codex_app
# entry on the WSL agent path, so chat creation/resume fails with
# "invalid transport in mcp_servers.codex_app". The Windows-native path works.

[CmdletBinding()]
param(
    [switch]$Quiet,
    [string]$CodexHome
)

$ErrorActionPreference = 'Stop'

if (-not $CodexHome) { $CodexHome = Join-Path $env:USERPROFILE '.codex' }
$configPath = Join-Path $CodexHome 'config.toml'

if (-not (Test-Path -LiteralPath $configPath)) {
    if (-not $Quiet) { Write-Output '{"status":"missing_config"}' }
    return
}

if ($env:CODEX_ALLOW_WSL -eq '1') {
    if (-not $Quiet) { Write-Output '{"status":"wsl_allowed"}' }
    return
}

$lines = [System.Collections.Generic.List[string]](Get-Content -LiteralPath $configPath)
$pattern = '^\s*runCodexInWindowsSubsystemForLinux\s*='
$existing = $false

for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match $pattern) {
        $lines[$i] = 'runCodexInWindowsSubsystemForLinux = false'
        $existing = $true
    }
}

if (-not $existing) {
    $desktopIndex = -1
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match '^\s*\[desktop\]\s*$') {
            $desktopIndex = $i
            break
        }
    }

    if ($desktopIndex -ge 0) {
        $lines.Insert($desktopIndex + 1, 'runCodexInWindowsSubsystemForLinux = false')
    } else {
        if ($lines.Count -gt 0 -and $lines[$lines.Count - 1] -ne '') {
            $lines.Add('')
        }
        $lines.Add('[desktop]')
        $lines.Add('runCodexInWindowsSubsystemForLinux = false')
    }
}

[IO.File]::WriteAllLines($configPath, $lines, [Text.UTF8Encoding]::new($false))

if (-not $Quiet) {
    Write-Output '{"status":"wsl_native"}'
}
