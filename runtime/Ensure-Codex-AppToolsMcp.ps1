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
    # Use a line-end lookahead instead of `$`: on Windows, CRLF configs must
    # still match, and .NET multiline `$` does not anchor before `\r`.
    $headerPattern = '(?m)^[ \t]*\[mcp_servers\.codex_app\][ \t]*(?:#.*)?(?=\r?\n|$)'
    $match = [regex]::Match($text, $headerPattern)
    if (-not $match.Success) {
        return @{ status = 'absent'; path = $Path }
    }

    # Include child tables such as `[mcp_servers.codex_app.env]` in the
    # quarantined block. Stopping at the first child table would leave a
    # partial static definition behind.
    $headerRegex = [regex]::new('(?m)^[ \t]*\[[^\r\n]+\][ \t]*(?:#.*)?(?=\r?\n|$)')
    $next = $headerRegex.Match($text, $match.Index + $match.Length)
    while ($next.Success -and
        $next.Value.Trim() -match '^\[mcp_servers\.codex_app(?:\.[^\]]+)?\]') {
        $next = $headerRegex.Match($text, $next.Index + $next.Length)
    }
    $end = if ($next.Success) { $next.Index } else { $text.Length }
    $block = $text.Substring($match.Index, $end - $match.Index)

    # Only remove static user-level definitions. The Desktop-owned dynamic
    # definition is generated in memory and includes the per-session pipe path.
    # HOWEVER: legacy threads (pre-MCP-lane) store "dynamic_tools":[{"name":"codex_app"}]
    # and require a resolvable [mcp_servers.codex_app] block in config.toml to
    # resume. If the static block already carries a valid command/args/cwd, KEEP it
    # instead of deleting it, so those threads can load.
    $looksStatic =
        $block -match '(?m)^[ \t]*(command|url|args|cwd|transport|type|enabled)[ \t]*=' -or
        $block -match 'CODEX_APP_TOOLS_PIPE_PATH'
    if (-not $looksStatic) {
        return @{ status = 'skipped'; reason = 'no static transport fields'; path = $Path }
    }
    $hasValidTransport =
        $block -match '(?m)^[ \t]*command[ \t]*=' -and
        $block -match '(?m)^[ \t]*args[ \t]*=' -and
        $block -match '(?m)^[ \t]*cwd[ \t]*='
    if ($hasValidTransport) {
        # Keep the transport so legacy threads can still resolve `codex_app`, but
        # never let the sidecar start this server. A user-level block cannot carry
        # Desktop's per-session CODEX_APP_TOOLS_PIPE_PATH, so a spawn from here
        # always aborts at startup and leaves the codex_app tools unregistered
        # ("unsupported call: mcp__codex_app__automation_update"). Desktop's own
        # plugin descriptor (enabled=true plus env_vars) owns the live server, so
        # the disabled mirror and this block only have to satisfy config loading.
        $normalized = $block
        if ($normalized -match '(?m)^[ \t]*enabled[ \t]*=') {
            # The lookahead keeps the assignment CRLF-safe: `.` and `[ \t]*` never
            # consume the `\r`, so a `$` anchor alone silently misses on Windows
            # configs and leaves the server enabled.
            $normalized = [regex]::Replace(
                $normalized,
                '(?m)^([ \t]*enabled[ \t]*=[ \t]*)true(?=[ \t]*(?:#[^\r\n]*)?\r?$)',
                '${1}false')
        } else {
            # No `enabled` key means the sidecar defaults to enabled, so add one
            # inside the parent table, before any child table header.
            $child = [regex]::Match($normalized, '(?m)^[ \t]*\[mcp_servers\.codex_app\.[^\r\n]+\]')
            if ($child.Success) {
                $normalized = $normalized.Substring(0, $child.Index) +
                    ('enabled = false' + $newline) +
                    $normalized.Substring($child.Index)
            } else {
                $trimmed = $normalized.TrimEnd("`r", "`n")
                $tail = $normalized.Substring($trimmed.Length)
                $normalized = $trimmed + $newline + 'enabled = false' + $tail
            }
        }
        # A stale pipe path captured in an earlier session is worse than none:
        # it lets the server start and then talk to a dead pipe.
        $normalized = [regex]::Replace(
            $normalized,
            '(?m)^[ \t]*CODEX_APP_TOOLS_PIPE_PATH[ \t]*=[^\r\n]*\r?\n?',
            '')

        if ($normalized -ne $block) {
            $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
            $backup = "$Path.bak-before-codex-app-pipe-$stamp"
            Copy-Item -LiteralPath $Path -Destination $backup -Force

            $replacement = $text.Substring(0, $match.Index) + $normalized + $text.Substring($end)
            [IO.File]::WriteAllText($Path, $replacement, [Text.UTF8Encoding]::new($false))

            return @{
                status = 'kept-disabled'
                reason = 'static block kept for legacy transport, server disabled so Desktop owns the pipe'
                path = $Path
                backup = $backup
            }
        }

        return @{
            status = 'kept'
            reason = 'static block already disabled for legacy transport'
            path = $Path
        }
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
    throw
}
