$ErrorActionPreference = 'Stop'

$updater = Join-Path $PSScriptRoot 'update_sidecar_tools_sha.ps1'
$scratch = Join-Path ([IO.Path]::GetTempPath()) ('codex-sidecar-source-test-' + [guid]::NewGuid().ToString('N'))
$metadata = Join-Path $scratch '.sidecar-source'
$sha = '1234567890abcdef1234567890abcdef12345678'

try {
    New-Item -ItemType Directory -Force -Path $scratch | Out-Null
    [IO.File]::WriteAllText(
        $metadata,
        "upstream_tag=v1`ncodex_desktop_tools_sha=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`ncodex_source_sha=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb`n",
        [Text.UTF8Encoding]::new($false)
    )
    & $updater -Path $metadata -ToolsSha $sha
    $first = [IO.File]::ReadAllText($metadata)
    if ($first -notmatch "(?m)^codex_desktop_tools_sha=$sha$") {
        throw 'Tools SHA was not updated.'
    }
    if ($first -notmatch '(?m)^codex_source_sha=b{40}$') {
        throw 'Sidecar source SHA was not preserved.'
    }
    if ([IO.File]::ReadAllBytes($metadata)[0] -eq 0xEF) {
        throw 'Metadata writer introduced a UTF-8 BOM.'
    }

    & $updater -Path $metadata -ToolsSha $sha
    if ([IO.File]::ReadAllText($metadata) -cne $first) {
        throw 'Metadata update is not idempotent.'
    }

    [IO.File]::WriteAllText($metadata, "codex_source_sha=b`n", [Text.UTF8Encoding]::new($false))
    try {
        & $updater -Path $metadata -ToolsSha $sha
        throw 'Missing tools SHA unexpectedly passed.'
    } catch {
        if ($_.Exception.Message -notmatch 'Expected exactly one') { throw }
    }

    Write-Host 'PASS: repack runtime provenance updates exactly one tools SHA, preserves sidecar metadata and is idempotent.'
    $global:LASTEXITCODE = 0
} finally {
    if (Test-Path -LiteralPath $scratch) {
        Remove-Item -LiteralPath $scratch -Recurse -Force
    }
}
