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
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File Ensure-Codex-AppToolsMcp.ps1 [-Quiet]

[CmdletBinding()]
param(
    [switch]$Quiet,
    [string]$CodexHome
)

$ErrorActionPreference = 'Stop'

if (-not $CodexHome) { $CodexHome = Join-Path $env:USERPROFILE '.codex' }

$searchRoots = @(
    (Join-Path $CodexHome 'plugins\cache'),
    (Join-Path $CodexHome '.tmp\bundled-marketplaces')
)

$results = @()
try {
    foreach ($root in $searchRoots) {
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
        if ($results.Count -eq 0) {
            Write-Host '{"status":"nothing_to_do"}'
        } else {
            @{ status = 'done'; results = $results } | ConvertTo-Json -Depth 6 -Compress | Write-Host
        }
    }
} catch {
    if (-not $Quiet) {
        @{ status = 'error'; error = $_.Exception.Message } | ConvertTo-Json -Compress | Write-Host
    }
}
