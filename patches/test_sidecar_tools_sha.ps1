$ErrorActionPreference = 'Stop'

$updater = Join-Path $PSScriptRoot 'update_sidecar_tools_sha.ps1'
$scratch = Join-Path ([IO.Path]::GetTempPath()) ('codex-sidecar-source-test-' + [guid]::NewGuid().ToString('N'))
$metadata = Join-Path $scratch '.sidecar-source'
$sha = '1234567890abcdef1234567890abcdef12345678'

try {
    New-Item -ItemType Directory -Force -Path $scratch | Out-Null
    [IO.File]::WriteAllText(
        $metadata,
        "upstream_tag=v1`r`ncodex_desktop_tools_sha=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`r`ncodex_source_sha=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb`r`n",
        [Text.UTF8Encoding]::new($false)
    )
    & $updater -Path $metadata -ToolsSha $sha
    $first = [IO.File]::ReadAllText($metadata)
    if ($first -notmatch "(?m)^codex_desktop_tools_sha=$sha\r?$") {
        throw 'Tools SHA was not updated.'
    }
    if ($first -notmatch '(?m)^codex_source_sha=b{40}\r?$') {
        throw 'Sidecar source SHA was not preserved.'
    }
    if ($first -match '(?<!\r)\n') {
        throw 'CRLF metadata was rewritten with mixed line endings.'
    }
    if ([IO.File]::ReadAllBytes($metadata)[0] -eq 0xEF) {
        throw 'Metadata writer introduced a UTF-8 BOM.'
    }

    & $updater -Path $metadata -ToolsSha $sha
    if ([IO.File]::ReadAllText($metadata) -cne $first) {
        throw 'Metadata update is not idempotent.'
    }

    # Exercise the actual workflow predicate, not an independently corrected copy.
    $workflow = [IO.File]::ReadAllText(
        (Join-Path (Split-Path $PSScriptRoot -Parent) '.github\workflows\repack-existing-patched.yml')
    )
    $predicate = [regex]::Match($workflow, 'if \(\$sidecarMetadata -notmatch "([^"]+)"\)')
    if (-not $predicate.Success) { throw 'Repack provenance predicate was not found.' }
    $pattern = $predicate.Groups[1].Value.Replace('$env:GITHUB_SHA', $sha)
    foreach ($newline in @("`n", "`r`n", '')) {
        $valid = "codex_desktop_tools_sha=$sha$newline"
        if ($valid -notmatch $pattern) {
            throw 'Repack rejects valid tools provenance with a supported line ending.'
        }
        if ("codex_desktop_tools_sha=$('a' * 40)$newline" -match $pattern) {
            throw 'Repack accepts the wrong tools source SHA.'
        }
    }
    if ($first -notmatch $pattern) {
        throw 'Repack rejects the CRLF metadata produced by the provenance updater.'
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
