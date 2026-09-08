$ErrorActionPreference = 'Stop'

function Assert-True {
    param(
        [Parameter(Mandatory=$true)][bool]$Condition,
        [Parameter(Mandatory=$true)][string]$Message
    )

    if (-not $Condition) {
        throw $Message
    }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$updaterPath = Join-Path $repoRoot 'runtime\Update-Codex.ps1'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $updaterPath,
    [ref]$tokens,
    [ref]$parseErrors
)
if ($parseErrors.Count -gt 0) {
    throw "Updater parse failed: $($parseErrors[0].Message)"
}

foreach ($name in @('Get-CodexAssetState', 'Test-CodexReleaseStateMatch')) {
    $functionAst = @($ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq $name
    }, $true))[0]
    if (-not $functionAst) {
        throw "Could not find updater function $name"
    }
    Invoke-Expression $functionAst.Extent.Text
}

$asset = [pscustomobject]@{
    name = 'CodexDesktop-Patched-win-x64-v26.721.41059-patched.zip'
    digest = 'sha256:abc123'
    updated_at = '2026-07-29T05:00:00Z'
    size = 735248788
}
$state = Get-CodexAssetState -Tag 'v26.721.41059-patched' -Asset $asset

Assert-True (
    Test-CodexReleaseStateMatch -State $state -Tag 'v26.721.41059-patched' -Asset $asset
) 'Matching tag and digest should be current.'

$wrongDigest = $state | Select-Object *
$wrongDigest.assetDigest = 'sha256:old'
Assert-True (-not (
    Test-CodexReleaseStateMatch -State $wrongDigest -Tag 'v26.721.41059-patched' -Asset $asset
)) 'A replaced same-tag asset must require an update.'

# The real incident this guard exists for: `v26.901.51231-patched` was deleted and
# republished with a different digest, size and timestamp while every installed copy
# kept the same version string. Two machines under one tag must not look identical.
$republishedAsset = [pscustomobject]@{
    name = 'CodexDesktop-Patched-win-x64-v26.901.51231-patched.zip'
    digest = 'sha256:d41723c618b4060def39eec0e5fd78753aefcd13cc0c70cd95f89dc24164fea3'
    updated_at = '2026-09-08T10:12:09Z'
    size = 791841359
}
$installedBeforeRepublish = [pscustomobject]@{
    schemaVersion = 1
    tag = 'v26.901.51231-patched'
    assetName = $republishedAsset.name
    assetDigest = 'sha256:c97600ba6df933cb270b7815782e4be3a47b76707d5e7ba053af4ffb26f3dfa7'
    assetUpdatedAt = '2026-09-06T01:24:12Z'
    assetSize = 790740866
}
Assert-True (-not (
    Test-CodexReleaseStateMatch `
        -State $installedBeforeRepublish `
        -Tag 'v26.901.51231-patched' `
        -Asset $republishedAsset
)) 'A copy installed before a republish must not be treated as current.'

$installedAfterRepublish = Get-CodexAssetState `
    -Tag 'v26.901.51231-patched' `
    -Asset $republishedAsset
Assert-True (
    Test-CodexReleaseStateMatch `
        -State $installedAfterRepublish `
        -Tag 'v26.901.51231-patched' `
        -Asset $republishedAsset
) 'A copy installed from the republished digest is current.'

Assert-True (-not (
    Test-CodexReleaseStateMatch -State $null -Tag 'v26.721.41059-patched' -Asset $asset
)) 'Missing release state must trigger a one-time refresh.'

$assetWithoutDigest = [pscustomobject]@{
    name = $asset.name
    digest = ''
    updated_at = $asset.updated_at
    size = $asset.size
}
$fallbackState = Get-CodexAssetState -Tag 'v26.721.41059-patched' -Asset $assetWithoutDigest
Assert-True (
    Test-CodexReleaseStateMatch `
        -State $fallbackState `
        -Tag 'v26.721.41059-patched' `
        -Asset $assetWithoutDigest
) 'Timestamp and size fallback should match when GitHub omits a digest.'

$fallbackState.assetSize++
Assert-True (-not (
    Test-CodexReleaseStateMatch `
        -State $fallbackState `
        -Tag 'v26.721.41059-patched' `
        -Asset $assetWithoutDigest
)) 'Fallback size changes must require an update.'

Write-Host 'Updater release asset state tests passed.'
