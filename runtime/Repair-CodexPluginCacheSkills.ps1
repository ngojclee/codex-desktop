# Repair-CodexPluginCacheSkills.ps1
#
# Why this exists
# ---------------
# Codex Desktop materialises each bundled plugin from the runtime marketplace
# (~\.codex\.tmp\bundled-marketplaces\openai-bundled\plugins\<plugin>) into the user
# plugin cache (~\.codex\plugins\cache\openai-bundled\<plugin>\<version>). If a single
# file fails to copy, the plugin's own installer never retries: it logs
# `bundled_plugin_install_skipped_current pluginName=<plugin>` on every boot because the
# version already matches.
#
# Measured on 10.11.1.1: `chrome` had 381 files in the cache and 382 in the marketplace
# with identical counts in every subdirectory except `skills` (0 vs 1). The missing file
# was `skills/control-chrome/SKILL.md`, which is why `$Chrome` vanished from the plugin
# and skill list while the plugin still existed on disk and stayed `enabled = true`.
#
# This helper copies only what is missing. It never deletes, so it cannot damage a
# working install, and it is safe to run on every launch.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File Repair-CodexPluginCacheSkills.ps1 [-Quiet]

[CmdletBinding()]
param(
    [string]$CodexHome,
    [string]$MarketplaceRoot,
    [string]$CacheRoot,
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'

if (-not $CodexHome) { $CodexHome = Join-Path $env:USERPROFILE '.codex' }
if (-not $MarketplaceRoot) {
    $MarketplaceRoot = Join-Path $CodexHome '.tmp\bundled-marketplaces\openai-bundled\plugins'
}
if (-not $CacheRoot) {
    $CacheRoot = Join-Path $CodexHome 'plugins\cache\openai-bundled'
}

function Write-Note {
    param([string]$Message)
    if (-not $Quiet) { Write-Host $Message }
}

if (-not (Test-Path -LiteralPath $MarketplaceRoot)) {
    Write-Note "SKIP: marketplace plugin root not found: $MarketplaceRoot"
    return [PSCustomObject]@{ status = 'skipped'; reason = 'marketplace-missing'; restored = 0 }
}
if (-not (Test-Path -LiteralPath $CacheRoot)) {
    Write-Note "SKIP: plugin cache root not found: $CacheRoot"
    return [PSCustomObject]@{ status = 'skipped'; reason = 'cache-missing'; restored = 0 }
}

# The cache holds one or more version directories plus a `latest` link/dir. Pick the
# directory that actually carries a plugin manifest, preferring the highest version.
function Resolve-CacheVersionDir {
    param([string]$PluginCacheDir)

    $candidates = @(
        Get-ChildItem -LiteralPath $PluginCacheDir -Directory -Force -EA SilentlyContinue |
            Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName '.codex-plugin\plugin.json') }
    )
    if ($candidates.Count -eq 0) { return $null }
    return ($candidates | Sort-Object Name -Descending | Select-Object -First 1).FullName
}

$restoredFiles = New-Object System.Collections.Generic.List[string]
$checkedPlugins = 0

foreach ($pluginDir in @(Get-ChildItem -LiteralPath $MarketplaceRoot -Directory -Force -EA SilentlyContinue)) {
    $sourceSkills = Join-Path $pluginDir.FullName 'skills'
    if (-not (Test-Path -LiteralPath $sourceSkills)) { continue }

    $cachePluginDir = Join-Path $CacheRoot $pluginDir.Name
    if (-not (Test-Path -LiteralPath $cachePluginDir)) {
        Write-Note ("{0}: not materialised yet, leaving it to the app installer" -f $pluginDir.Name)
        continue
    }
    $targetRoot = Resolve-CacheVersionDir -PluginCacheDir $cachePluginDir
    if (-not $targetRoot) {
        Write-Note ("{0}: cache present but no version manifest, skipping" -f $pluginDir.Name)
        continue
    }
    $checkedPlugins++

    # [IO.Path]::GetRelativePath needs .NET Core and is absent from Windows PowerShell
    # 5.1, which is what the desktop shortcut chain can invoke, so compute the relative
    # path from the known prefix instead.
    $sourcePrefix = $sourceSkills.TrimEnd('\') + '\'
    foreach ($sourceFile in @(Get-ChildItem -LiteralPath $sourceSkills -Recurse -File -Force -EA SilentlyContinue)) {
        $relative = $sourceFile.FullName.Substring($sourcePrefix.Length)
        $targetFile = Join-Path (Join-Path $targetRoot 'skills') $relative
        if (Test-Path -LiteralPath $targetFile) { continue }
        $targetParent = Split-Path -Parent $targetFile
        New-Item -ItemType Directory -Force -Path $targetParent | Out-Null
        Copy-Item -LiteralPath $sourceFile.FullName -Destination $targetFile -Force
        $restoredFiles.Add("$($pluginDir.Name)/$relative") | Out-Null
    }
}

if ($restoredFiles.Count -gt 0) {
    Write-Note ("Restored {0} missing plugin file(s):" -f $restoredFiles.Count)
    foreach ($entry in $restoredFiles) { Write-Note ("  $entry") }
    Write-Note 'Restart Codex Desktop (or open a new session) so the plugin skills are re-read.'
} else {
    Write-Note ("All skill files present across {0} materialised plugin(s)." -f $checkedPlugins)
}

return [PSCustomObject]@{
    status        = 'done'
    checked       = $checkedPlugins
    restored      = $restoredFiles.Count
    restoredFiles = @($restoredFiles)
}
