# Full renderer acceptance on an extracted, disposable app directory.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$AppDir,
    [string]$UpstreamTag
)

$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'apply-all-patches.ps1') -AppDir $AppDir -UpstreamTag $UpstreamTag
& python (Join-Path $PSScriptRoot 'patches/verify_markers.py') $AppDir --upstream-tag $UpstreamTag
if ($LASTEXITCODE -ne 0) { throw "Release verifier failed: $LASTEXITCODE" }
Write-Host 'PASS: renderer patches, syntax, packed file/block integrity and exe/header integrity'
