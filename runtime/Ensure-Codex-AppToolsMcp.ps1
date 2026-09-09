#!/usr/bin/env pwsh
# Ensure-Codex-AppToolsMcp.ps1
#
# Keeps a sidecar-readable `.mcp.json` next to every plugin that only ships the
# newer `desktop-mcp.json`.
#
# Why: Codex Desktop 26.825 renamed the bundled `codex-app-tools` MCP definition
# from `.mcp.json` to `desktop-mcp.json`. Only the Electron app reads the new
# name, so the app-server no longer learns the `codex_app` transport. The app
# still writes `mcp_servers.codex_app.enabled_tools`, which then lands on a
# transport-less entry and the sidecar rejects the whole config with
# "failed to load configuration: invalid transport in `mcp_servers.codex_app`",
# surfacing in the UI as "Error creating chat".
#
# The mirror is written with `enabled = false`, matching plugin 0.1.0, so the
# sidecar can resolve the transport without launching a duplicate server.
#
# Older recovery guidance also added a static `[mcp_servers.codex_app]` block to
# `~/.codex/config.toml`. That block is now harmful: the app-tools MCP server
# needs a per-session `CODEX_APP_TOOLS_PIPE_PATH` that Desktop injects only when
# it builds the dynamic app-tools config. A static user block starts the server
# without that pipe and every codex_app resource/tool call fails at startup.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File Ensure-Codex-AppToolsMcp.ps1 [-Quiet]

[CmdletBinding()]
param(
    [switch]$Quiet,
    [string]$CodexHome,
    [string]$InstallDir,
    [string]$ConfigPath
)

$ErrorActionPreference = 'Stop'

if (-not $CodexHome) { $CodexHome = Join-Path $env:USERPROFILE '.codex' }
if (-not $InstallDir) { $InstallDir = Join-Path $env:LOCALAPPDATA 'CodexFromGithub' }
if (-not $ConfigPath) { $ConfigPath = Join-Path $CodexHome 'config.toml' }

function Remove-StaticCodexAppServerConfig {
    param([Parameter(Mandatory=$true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return @{ status = 'missing'; path = $Path }
    }

    $text = [IO.File]::ReadAllText($Path)
    $newline = if ($text -match "`r`n") { "`r`n" } else { "`n" }
    $headerPattern = '(?m)^[ \t]*\[mcp_servers\.codex_app\][ \t]*(?:#.*)?$'
    $match = [regex]::Match($text, $headerPattern)
    if (-not $match.Success) {
        return @{ status = 'absent'; path = $Path }
    }

    # Include child tables such as `[mcp_servers.codex_app.env]` in the
    # quarantined block. Stopping at the first child table would leave a
    # partial static definition behind.
    $headerRegex = [regex]::new('(?m)^[ \t]*\[[^\r\n]+\][ \t]*(?:#.*)?$')
    $next = $headerRegex.Match($text, $match.Index + $match.Length)
    while ($next.Success -and
        $next.Value.Trim() -match '^\[mcp_servers\.codex_app(?:\.[^\]]+)?\]') {
        $next = $headerRegex.Match($text, $next.Index + $next.Length)
    }
    $end = if ($next.Success) { $next.Index } else { $text.Length }
    $block = $text.Substring($match.Index, $end - $match.Index)

    # Only remove static user-level definitions. The Desktop-owned dynamic
    # definition is generated in memory and includes the per-session pipe path.
    $looksStatic =
        $block -match '(?m)^[ \t]*(command|url|args|cwd|transport|type|enabled)[ \t]*=' -or
        $block -match 'CODEX_APP_TOOLS_PIPE_PATH'
    if (-not $looksStatic) {
        return @{ status = 'skipped'; reason = 'no static transport fields'; path = $Path }
    }

    $commentStart = $match.Index
    $prefix = $text.Substring(0, $match.Index)
    $knownCommentPattern = '(?ms)(?:^|\r?\n)(# Workaround for upstream bug.*?# an explicit command-based block here overrides the injected one\.\r?\n)$'
    $commentMatch = [regex]::Match($prefix, $knownCommentPattern)
    if ($commentMatch.Success -and $commentMatch.Index + $commentMatch.Length -eq $prefix.Length) {
        if ($commentMatch.Value.StartsWith("`n") -or $commentMatch.Value.StartsWith("`r`n")) {
            $commentStart = $commentMatch.Index + ($commentMatch.Value.Length - $commentMatch.Groups[1].Value.Length)
        } else {
            $commentStart = $commentMatch.Index
        }
    }

    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $backup = "$Path.bak-before-codex-app-pipe-$stamp"
    Copy-Item -LiteralPath $Path -Destination $backup -Force

    $before = $text.Substring(0, $commentStart)
    $after = $text.Substring($end)
    $replacement = $before.TrimEnd("`r", "`n") + $newline + $newline + $after.TrimStart("`r", "`n")
    [IO.File]::WriteAllText($Path, $replacement, [Text.UTF8Encoding]::new($false))

    return @{
        status = 'removed'
        path = $Path
        backup = $backup
        removedBytes = [Text.Encoding]::UTF8.GetByteCount($block)
    }
}

function Test-DynamicAppToolsPipeSupport {
    param([Parameter(Mandatory=$true)][string]$Path)

    $asar = Join-Path $Path 'resources\app.asar'
    if (-not (Test-Path -LiteralPath $asar)) { return $false }

    try {
        # The current Desktop app owns pipe creation. This literal is its
        # stable source/runtime capability marker and avoids removing the
        # legacy config workaround from an older bundle that cannot inject it.
        $bytes = [IO.File]::ReadAllBytes($asar)
        $text = [Text.Encoding]::ASCII.GetString($bytes)
        return $text.Contains('CODEX_APP_TOOLS_PIPE_PATH')
    } catch {
        return $false
    }
}

$searchRoots = @(
    (Join-Path $CodexHome 'plugins\cache'),
    (Join-Path $CodexHome '.tmp\bundled-marketplaces'),
    (Join-Path $InstallDir 'resources\plugins')
)

$results = @()
try {
    if (Test-DynamicAppToolsPipeSupport -Path $InstallDir) {
        $configRepair = Remove-StaticCodexAppServerConfig -Path $ConfigPath
    } else {
        $configRepair = @{
            status = 'skipped'
            reason = 'desktop bundle has no dynamic app-tools pipe marker'
            path = $ConfigPath
        }
    }

    foreach ($root in ($searchRoots | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        $definitions = @(
            Get-ChildItem -LiteralPath $root -Recurse -File -Force -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -eq 'desktop-mcp.json' }
        )
        foreach ($src in $definitions) {
            $dst = Join-Path $src.DirectoryName '.mcp.json'
            if (Test-Path -LiteralPath $dst) {
                $results += @{ status = 'exists'; path = $dst }
                continue
            }
            try {
                $json = Get-Content -LiteralPath $src.FullName -Raw | ConvertFrom-Json
                $servers = $json.mcpServers
                if ($null -eq $servers) {
                    $results += @{ status = 'skipped'; reason = 'no mcpServers'; path = $dst }
                    continue
                }
                foreach ($server in $servers.PSObject.Properties) {
                    $fieldNames = @($server.Value.PSObject.Properties.Name)
                    if (($fieldNames -notcontains 'command') -and ($fieldNames -notcontains 'url')) {
                        # Nothing to salvage without a transport.
                        continue
                    }
                    if ($server.Value.PSObject.Properties.Name -contains 'enabled') {
                        $server.Value.enabled = $false
                    } else {
                        $server.Value | Add-Member -NotePropertyName enabled -NotePropertyValue $false
                    }
                }
                $payload = ($json | ConvertTo-Json -Depth 12) + "`n"
                [IO.File]::WriteAllText($dst, $payload, [Text.UTF8Encoding]::new($false))
                $results += @{ status = 'created'; path = $dst }
            } catch {
                $results += @{ status = 'error'; path = $dst; error = $_.Exception.Message }
            }
        }
    }

    if (-not $Quiet) {
        if ($results.Count -eq 0 -and $configRepair.status -in @('absent', 'missing', 'skipped')) {
            Write-Host '{"status":"nothing_to_do"}'
        } else {
            @{
                status = 'done'
                config = $configRepair
                results = $results
            } | ConvertTo-Json -Depth 6 -Compress | Write-Host
        }
    }
} catch {
    if (-not $Quiet) {
        @{ status = 'error'; error = $_.Exception.Message } | ConvertTo-Json -Compress | Write-Host
    }
}
