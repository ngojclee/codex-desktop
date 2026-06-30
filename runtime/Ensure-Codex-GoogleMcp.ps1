# Ensure-Codex-GoogleMcp.ps1
#
# Keeps Codex's Google Drive MCP entries durable across patched Desktop
# installs. The Google Drive plugin skills are bundled/cache-managed by Codex;
# endpoint wiring belongs in ~/.codex/config.toml instead.

[CmdletBinding()]
param(
    [string]$CodexHome = (Join-Path $env:USERPROFILE '.codex'),
    [string]$Endpoint = $env:CODEX_GOOGLE_MCP_URL,
    [double]$StartupTimeoutSec = 45.0,
    [int]$BackupKeep = 5,
    [switch]$Quiet,
    [switch]$Json
)

$ErrorActionPreference = 'Stop'

if (-not $Endpoint) {
    $Endpoint = 'http://10.21.4.101:3110/mcp'
}

$disable = [string]$env:CODEX_GOOGLE_MCP_DISABLE
if ($disable -match '^(1|true|yes|on)$') {
    $result = [ordered]@{
        changed = $false
        disabled = $true
        config = (Join-Path $CodexHome 'config.toml')
        endpoint = $Endpoint
    }
    if ($Json) {
        $result | ConvertTo-Json -Compress
    } elseif (-not $Quiet) {
        Write-Host 'Google MCP config ensure skipped because CODEX_GOOGLE_MCP_DISABLE is set.'
    }
    return
}

$serverNames = @(
    'google-drive',
    'google-sheets',
    'google-docs',
    'google-slides',
    'google-drive-comments'
)

$configPath = Join-Path $CodexHome 'config.toml'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function ConvertTo-TomlString {
    param([Parameter(Mandatory=$true)][string]$Value)

    return '"' + (($Value -replace '\\', '\\\\') -replace '"', '\"') + '"'
}

function Get-KeyIndex {
    param(
        [Parameter(Mandatory=$true)]$Lines,
        [Parameter(Mandatory=$true)][int]$Start,
        [Parameter(Mandatory=$true)][int]$End,
        [Parameter(Mandatory=$true)][string]$Key
    )

    $pattern = '^\s*' + [regex]::Escape($Key) + '\s*='
    for ($i = $Start + 1; $i -lt $End; $i++) {
        if ($Lines[$i] -match $pattern) { return $i }
    }
    return -1
}

function Find-Section {
    param(
        [Parameter(Mandatory=$true)]$Lines,
        [Parameter(Mandatory=$true)][string]$Header
    )

    for ($i = 0; $i -lt $Lines.Count; $i++) {
        if ($Lines[$i].Trim() -eq $Header) {
            $end = $Lines.Count
            for ($j = $i + 1; $j -lt $Lines.Count; $j++) {
                if ($Lines[$j] -match '^\s*\[') {
                    $end = $j
                    break
                }
            }
            return [PSCustomObject]@{ Start = $i; End = $end }
        }
    }

    return $null
}

function Ensure-GoogleMcpSection {
    param(
        [Parameter(Mandatory=$true)]$Lines,
        [Parameter(Mandatory=$true)][string]$ServerName,
        [Parameter(Mandatory=$true)][string]$EndpointValue,
        [Parameter(Mandatory=$true)][double]$TimeoutValue
    )

    $header = "[mcp_servers.$ServerName]"
    $urlLine = 'url = ' + (ConvertTo-TomlString $EndpointValue)
    $timeoutLine = 'startup_timeout_sec = ' + ([string]::Format([Globalization.CultureInfo]::InvariantCulture, '{0:0.0}', $TimeoutValue))
    $changed = $false

    $section = Find-Section -Lines $Lines -Header $header
    if (-not $section) {
        if ($Lines.Count -gt 0 -and $Lines[$Lines.Count - 1] -ne '') {
            [void]$Lines.Add('')
        }
        [void]$Lines.Add($header)
        [void]$Lines.Add($urlLine)
        [void]$Lines.Add($timeoutLine)
        return $true
    }

    $urlIndex = Get-KeyIndex -Lines $Lines -Start $section.Start -End $section.End -Key 'url'
    if ($urlIndex -ge 0) {
        if ($Lines[$urlIndex] -ne $urlLine) {
            $Lines[$urlIndex] = $urlLine
            $changed = $true
        }
    } else {
        $Lines.Insert($section.End, $urlLine)
        $section.End++
        $changed = $true
    }

    $timeoutIndex = Get-KeyIndex -Lines $Lines -Start $section.Start -End $section.End -Key 'startup_timeout_sec'
    if ($timeoutIndex -ge 0) {
        if ($Lines[$timeoutIndex] -ne $timeoutLine) {
            $Lines[$timeoutIndex] = $timeoutLine
            $changed = $true
        }
    } else {
        $insertAt = if ($urlIndex -ge 0) { $urlIndex + 1 } else { $section.End }
        $Lines.Insert($insertAt, $timeoutLine)
        $changed = $true
    }

    return $changed
}

New-Item -ItemType Directory -Force -Path $CodexHome | Out-Null

$originalText = ''
if (Test-Path -LiteralPath $configPath) {
    $originalText = [IO.File]::ReadAllText($configPath)
}

$newline = if ($originalText -match "`r`n") { "`r`n" } else { "`n" }
$lines = New-Object 'System.Collections.Generic.List[string]'
if ($originalText.Length -gt 0) {
    $split = [regex]::Split($originalText, '\r?\n')
    $endsWithNewline = ($originalText.EndsWith("`n") -or $originalText.EndsWith("`r"))
    $max = if ($endsWithNewline) { $split.Count - 1 } else { $split.Count }
    for ($i = 0; $i -lt $max; $i++) {
        [void]$lines.Add($split[$i])
    }
}

$changed = $false
foreach ($serverName in $serverNames) {
    if (Ensure-GoogleMcpSection -Lines $lines -ServerName $serverName -EndpointValue $Endpoint -TimeoutValue $StartupTimeoutSec) {
        $changed = $true
    }
}

if ($changed) {
    if (Test-Path -LiteralPath $configPath) {
        $backup = "$configPath.bak-googlemcp-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        Copy-Item -LiteralPath $configPath -Destination $backup -Force
    }

    $newText = [string]::Join($newline, $lines)
    if ($newText.Length -gt 0) { $newText += $newline }
    [IO.File]::WriteAllText($configPath, $newText, $utf8NoBom)

    if ($BackupKeep -ge 0) {
        Get-ChildItem -LiteralPath $CodexHome -Filter 'config.toml.bak-googlemcp-*' -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -Skip $BackupKeep |
            Remove-Item -Force -ErrorAction SilentlyContinue
    }
}

$result = [ordered]@{
    changed = [bool]$changed
    disabled = $false
    config = $configPath
    endpoint = $Endpoint
    servers = $serverNames
}

if ($Json) {
    $result | ConvertTo-Json -Compress
} elseif (-not $Quiet) {
    if ($changed) {
        Write-Host "Google MCP config updated: $configPath"
    } else {
        Write-Host "Google MCP config already up to date: $configPath"
    }
}
