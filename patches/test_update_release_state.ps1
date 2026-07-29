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
