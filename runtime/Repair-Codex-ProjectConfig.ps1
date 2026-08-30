#!/usr/bin/env pwsh
# Remove stale [projects."..."] config tables that newer Codex builds reject
# when their workspace path cannot be reached.

[CmdletBinding()]
param(
    [string]$ConfigPath = (Join-Path $env:USERPROFILE '.codex\config.toml'),
    [switch]$Apply,
    [switch]$RemoveAll,
    [int]$BackupKeep = 3,
    [switch]$Json
)

$ErrorActionPreference = 'Stop'

function Write-Result($Value) {
    if ($Json) {
        $Value | ConvertTo-Json -Depth 12 -Compress
    } else {
        $Value | ConvertTo-Json -Depth 12
    }
}

function ConvertFrom-ProjectTableKey([string]$Token) {
    $token = $Token.Trim()
    if ($token.StartsWith('"') -and $token.EndsWith('"')) {
        return [string](ConvertFrom-Json $token)
    }
    if ($token.StartsWith("'") -and $token.EndsWith("'")) {
        return $token.Substring(1, $token.Length - 2).Replace("''", "'")
    }
    throw "Unsupported project table key: $Token"
}

function Test-ReachablePath([string]$Path) {
    try {
        return [bool](Test-Path -LiteralPath $Path -ErrorAction Stop)
    } catch {
        return $false
    }
}

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    Write-Result ([ordered]@{
        status = 'missing_config'
        config = $ConfigPath
        changed = $false
        removed = @()
    })
    exit 0
}

$original = [IO.File]::ReadAllText($ConfigPath)
$newline = if ($original -match "`r`n") { "`r`n" } else { "`n" }
$rawLines = [regex]::Split($original, '\r?\n')
$endsWithNewline = $original.EndsWith("`n") -or $original.EndsWith("`r")
$lineCount = if ($endsWithNewline) { $rawLines.Count - 1 } else { $rawLines.Count }
$lines = @($rawLines[0..([Math]::Max(0, $lineCount - 1))])

$headerPattern = '^\s*\[projects\.(?<key>"(?:\\.|[^"])*"|''(?:''''|[^''])*'')\]\s*$'
$output = New-Object 'System.Collections.Generic.List[string]'
$removed = New-Object 'System.Collections.Generic.List[object]'
$currentProject = $null
$skipCurrent = $false

foreach ($line in $lines) {
    if ($line -match '^\s*\[') {
        $currentProject = $null
        $skipCurrent = $false
        if ($line -match $headerPattern) {
            $projectPath = ConvertFrom-ProjectTableKey $Matches.key
            $reachable = Test-ReachablePath $projectPath
            if ($RemoveAll -or -not $reachable) {
                $currentProject = $projectPath
                $skipCurrent = $true
                $reason = if ($RemoveAll) { 'remove_all' } else { 'unreachable' }
                [void]$removed.Add([ordered]@{
                    path = $projectPath
                    reason = $reason
                })
            }
        }
    }

    if (-not $skipCurrent) {
        [void]$output.Add($line)
    }
}

$changed = $removed.Count -gt 0
$status = if ($changed) {
    if ($Apply) { 'repaired' } else { 'preview' }
} else {
    'unchanged'
}
$backup = $null
if ($changed -and $Apply) {
    $backup = "$ConfigPath.bak-projects-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Copy-Item -LiteralPath $ConfigPath -Destination $backup -Force
    $text = [string]::Join($newline, $output)
    $text = $text.TrimEnd([char[]]"`r`n") + $newline
    [IO.File]::WriteAllText($ConfigPath, $text, [Text.UTF8Encoding]::new($false))

    if ($BackupKeep -ge 0) {
        Get-ChildItem -LiteralPath (Split-Path -Parent $ConfigPath) `
            -Filter 'config.toml.bak-projects-*' -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -Skip $BackupKeep |
            Remove-Item -Force -ErrorAction SilentlyContinue
    }
}

$removedRecords = $removed.ToArray()
Write-Result ([ordered]@{
    status = $status
    config = $ConfigPath
    changed = [bool]($changed -and $Apply)
    remove_all = [bool]$RemoveAll
    backup = $backup
    removed = $removedRecords
})
