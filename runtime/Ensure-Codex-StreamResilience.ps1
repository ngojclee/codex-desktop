#!/usr/bin/env pwsh
# Ensure-Codex-StreamResilience.ps1
#
# Keeps the long-stream network resilience keys durable inside the
# [model_providers.<id>] block of ~/.codex/config.toml.
#
# Why: long streaming turns through proxies can drop mid-stream and Codex
# treats a truncated body as "error decoding response body" (openai/codex
# #29087, #3478). The provider-level knobs below raise the reconnect budget so
# a dropped stream survives instead of killing the turn after the built-in
# defaults (request_max_retries=4, stream_max_retries=5,
# stream_idle_timeout_ms=300000). Verified against openai/codex
# codex-rs/model-provider-info/src/lib.rs: these are per-provider fields, so
# they must live INSIDE the provider block -- top-level copies are ignored.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File Ensure-Codex-StreamResilience.ps1 [-Quiet]

[CmdletBinding()]
param(
    [string]$ProviderId = 'cliproxy',
    [int]$RequestMaxRetries = 8,
    [int]$StreamMaxRetries = 20,
    [int]$StreamIdleTimeoutMs = 600000,
    [switch]$Quiet,
    [string]$ConfigPath
)

$ErrorActionPreference = 'Stop'

if (-not $ConfigPath) {
    $ConfigPath = Join-Path $env:USERPROFILE '.codex\config.toml'
}

function Write-Result([hashtable]$Status) {
    if (-not $Quiet) {
        $Status | ConvertTo-Json -Compress | Write-Host
    }
}

try {
    if (-not (Test-Path -LiteralPath $ConfigPath)) {
        Write-Result @{ status = 'missing_config'; path = $ConfigPath }
        exit 0
    }

    # Read/write explicitly as UTF-8 (no BOM on write): the file may contain
    # non-ASCII project paths and Windows PowerShell 5.1 otherwise assumes
    # ANSI when reading without a BOM.
    $text = [IO.File]::ReadAllText($ConfigPath)
    # Normalize everything to LF so line indexing stays simple; Codex accepts
    # either newline style.
    $normalized = $text.Replace("`r`n", "`n")
    $lines = @([regex]::Split($normalized, '\n'))

    $headerPattern = '^\[model_providers\.' + [regex]::Escape($ProviderId) + '\]\s*$'
    $headerIndex = -1
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match $headerPattern) {
            $headerIndex = $i
            break
        }
    }
    if ($headerIndex -lt 0) {
        Write-Result @{ status = 'provider_missing'; provider = $ProviderId; path = $ConfigPath }
        exit 0
    }

    $endIndex = $lines.Count
    for ($i = $headerIndex + 1; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match '^\s*\[') {
            $endIndex = $i
            break
        }
    }

    # Every key appended before the next section header still belongs to this
    # table (blank lines inside a table body are legal TOML), so inserting at
    # $endIndex is always safe.
    $desired = [ordered]@{
        request_max_retries    = $RequestMaxRetries
        stream_max_retries     = $StreamMaxRetries
        stream_idle_timeout_ms = $StreamIdleTimeoutMs
    }

    $changedKeys = @()
    $addedKeys = @()
    $commentAdded = $false
    foreach ($key in $desired.Keys) {
        $value = $desired[$key]
        $escapedKey = [regex]::Escape($key)
        $hasKeyPattern = '(?m)^\s*' + $escapedKey + '\s*='
        $sectionText = ($lines[$headerIndex..($endIndex - 1)] -join "`n")
        $sectionHasKey = $sectionText -match $hasKeyPattern
        if ($sectionHasKey) {
            $valuePattern = '(?m)^(\s*' + $escapedKey + '\s*=\s*)([^#\n]*)(.*)$'
            $replacement = '${1}' + $value + '${3}'
            $matcher = [regex]$valuePattern
            $newSection = $matcher.Replace($sectionText, $replacement, 1)
            if ($newSection -ne $sectionText) {
                $replacedLines = @($newSection -split '\n')
                for ($i = $headerIndex; $i -lt $endIndex; $i++) {
                    $lines[$i] = $replacedLines[$i - $headerIndex]
                }
                $changedKeys += $key
            }
        } else {
            $blockLines = @("$key = $value")
            if (-not $commentAdded) {
                $blockLines = @(
                    '# stream resilience for long tasks (per-provider fields from openai/codex)'
                ) + $blockLines
                $commentAdded = $true
            }
            $head = @() + $lines[0..($endIndex - 1)]
            $tail = @()
            if ($endIndex -lt $lines.Count) {
                $tail = @() + $lines[$endIndex..($lines.Count - 1)]
            }
            $lines = @($head + $blockLines + $tail)
            $addedKeys += $key
            # The block grew by two lines; keep indices valid for further keys.
            $endIndex += $blockLines.Count
        }
    }

    if (($changedKeys.Count -eq 0) -and ($addedKeys.Count -eq 0)) {
        Write-Result @{ status = 'unchanged'; provider = $ProviderId; path = $ConfigPath }
        exit 0
    }

    $newText = $lines -join "`n"
    [IO.File]::WriteAllText($ConfigPath, $newText, [Text.UTF8Encoding]::new($false))
    Write-Result @{
        status   = 'updated'
        provider = $ProviderId
        updated  = $changedKeys
        added    = $addedKeys
        path     = $ConfigPath
    }
} catch {
    Write-Result @{ status = 'error'; error = $_.Exception.Message }
}
