[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Path,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ToolsSha
)

$ErrorActionPreference = 'Stop'

$resolved = (Resolve-Path -LiteralPath $Path).ProviderPath
$text = [IO.File]::ReadAllText($resolved)
$pattern = '(?m)^codex_desktop_tools_sha=[^\r\n]*(\r?)$'
$matches = [regex]::Matches($text, $pattern)
if ($matches.Count -ne 1) {
    throw "Expected exactly one codex_desktop_tools_sha entry in $resolved; found $($matches.Count)."
}

$normalizedSha = $ToolsSha.ToLowerInvariant()
$updated = [regex]::Replace($text, $pattern, {
    param($match)
    return 'codex_desktop_tools_sha=' + $normalizedSha + $match.Groups[1].Value
})
[IO.File]::WriteAllText($resolved, $updated, [Text.UTF8Encoding]::new($false))
Write-Host "Updated packaged runtime provenance: codex_desktop_tools_sha=$normalizedSha"
