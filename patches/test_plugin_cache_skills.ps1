# Regression test for Repair-CodexPluginCacheSkills.ps1.
#
# Reproduces the measured failure on 10.11.1.1: the cache copy of a plugin was complete
# except for a single skill file, and Desktop never retried the install because the
# version already matched. The repair must copy the missing file, must not touch a
# healthy plugin, must not invent a cache entry for a plugin the app has not
# materialised, and must be a no-op on a second run.

$ErrorActionPreference = 'Stop'

function Assert-True {
    param(
        [Parameter(Mandatory=$true)][bool]$Condition,
        [Parameter(Mandatory=$true)][string]$Message
    )
    if (-not $Condition) { throw $Message }
}

$root = Join-Path $env:TEMP ("codex-plugin-cache-test-" + [guid]::NewGuid().Guid)
$marketplace = Join-Path $root 'marketplace'
$cache = Join-Path $root 'cache'
$script = Join-Path $PSScriptRoot '..\runtime\Repair-CodexPluginCacheSkills.ps1'

function New-PluginFile {
    param([string]$Path, [string]$Content = 'x')
    New-Item -ItemType Directory -Force -Path (Split-Path $Path -Parent) | Out-Null
    [IO.File]::WriteAllText($Path, $Content, [Text.UTF8Encoding]::new($false))
}

try {
    # chrome-like plugin: cache exists, skill file missing (the real failure).
    New-PluginFile (Join-Path $marketplace 'chrome\skills\control-chrome\SKILL.md') 'chrome-skill'
    New-PluginFile (Join-Path $marketplace 'chrome\scripts\run.mjs') 'script'
    New-PluginFile (Join-Path $cache 'chrome\26.901.51231\.codex-plugin\plugin.json') '{"name":"chrome"}'
    New-PluginFile (Join-Path $cache 'chrome\26.901.51231\scripts\run.mjs') 'script'

    # healthy plugin: skill already present, must stay byte-identical.
    New-PluginFile (Join-Path $marketplace 'visualize\skills\visualize\SKILL.md') 'visualize-skill'
    New-PluginFile (Join-Path $cache 'visualize\1.0.29\.codex-plugin\plugin.json') '{"name":"visualize"}'
    New-PluginFile (Join-Path $cache 'visualize\1.0.29\skills\visualize\SKILL.md') 'visualize-skill'
    $healthyBefore = Get-FileHash (Join-Path $cache 'visualize\1.0.29\skills\visualize\SKILL.md') -Algorithm SHA256

    # not materialised yet: the repair must leave it to the app installer.
    New-PluginFile (Join-Path $marketplace 'depth\skills\depth\SKILL.md') 'depth-skill'

    $first = & $script -Quiet -MarketplaceRoot $marketplace -CacheRoot $cache
    Assert-True ($first.restored -eq 1) "First run should restore exactly one file, got $($first.restored)."
    Assert-True ($first.checked -eq 2) "First run should inspect two materialised plugins, got $($first.checked)."

    $restored = Join-Path $cache 'chrome\26.901.51231\skills\control-chrome\SKILL.md'
    Assert-True (Test-Path -LiteralPath $restored) 'Missing chrome skill file must be restored.'
    Assert-True ((Get-Content -LiteralPath $restored -Raw) -eq 'chrome-skill') 'Restored file content must match the marketplace copy.'
    Assert-True ((Get-FileHash (Join-Path $cache 'visualize\1.0.29\skills\visualize\SKILL.md') -Algorithm SHA256).Hash -eq $healthyBefore.Hash) `
        'A healthy plugin skill file must not be rewritten.'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $cache 'depth'))) `
        'A plugin the app has not materialised must not get a cache entry from this helper.'

    # The app puts scripts and other payload next to skills; the helper must only copy
    # skill files and never delete or rewrite the rest of the plugin.
    Assert-True ((Get-Content -LiteralPath (Join-Path $cache 'chrome\26.901.51231\scripts\run.mjs') -Raw) -eq 'script') `
        'Non-skill plugin payload must be left alone.'

    $second = & $script -Quiet -MarketplaceRoot $marketplace -CacheRoot $cache
    Assert-True ($second.restored -eq 0) "Second run must be a no-op, got $($second.restored)."

    'Plugin cache skill repair tests passed.'
} finally {
    Remove-Item -LiteralPath $root -Recurse -Force -EA SilentlyContinue
}
