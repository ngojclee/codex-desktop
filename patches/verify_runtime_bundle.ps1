[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RuntimeDir,
    [Parameter(Mandatory = $true)]
    [string]$ToolsDir
)

$ErrorActionPreference = 'Stop'

$runtimeRoot = (Resolve-Path -LiteralPath $RuntimeDir).ProviderPath
$toolsRoot = (Resolve-Path -LiteralPath $ToolsDir).ProviderPath

function Get-RelativeFileMap {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [string]$Filter
    )

    $items = if ($Filter) {
        Get-ChildItem -LiteralPath $Root -File -Recurse -Filter $Filter
    } else {
        Get-ChildItem -LiteralPath $Root -File -Recurse
    }

    $map = @{}
    foreach ($item in $items) {
        $relative = [IO.Path]::GetRelativePath($Root, $item.FullName).Replace('\', '/')
        $map[$relative.ToLowerInvariant()] = [PSCustomObject]@{
            Relative = $relative
            Path = $item.FullName
        }
    }
    return $map
}

$runtimeFiles = Get-RelativeFileMap -Root $runtimeRoot
foreach ($entry in $runtimeFiles.Values) {
    $destination = Join-Path $toolsRoot $entry.Relative
    if (-not (Test-Path -LiteralPath $destination -PathType Leaf)) {
        throw "Bundled tools verification failed: missing runtime file '$($entry.Relative)'"
    }

    $sourceHash = (Get-FileHash -LiteralPath $entry.Path -Algorithm SHA256).Hash
    $destinationHash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash
    if ($sourceHash -ne $destinationHash) {
        throw "Bundled tools verification failed: hash mismatch for '$($entry.Relative)'"
    }
}

# PowerShell helpers are maintained only under runtime/. Reject stale package-only
# scripts instead of allowing an old snapshot to survive a recursive overlay copy.
$runtimePowerShell = Get-RelativeFileMap -Root $runtimeRoot -Filter '*.ps1'
$toolsPowerShell = Get-RelativeFileMap -Root $toolsRoot -Filter '*.ps1'
$unexpected = @(
    $toolsPowerShell.GetEnumerator() |
        Where-Object { -not $runtimePowerShell.ContainsKey($_.Key) } |
        ForEach-Object { $_.Value.Relative } |
        Sort-Object
)
if ($unexpected.Count -gt 0) {
    throw "Bundled tools verification failed: package-only PowerShell helper(s): $($unexpected -join ', ')"
}

Write-Host "PASS: verified $($runtimeFiles.Count) runtime file(s); package tools contains no stale PowerShell helpers."
