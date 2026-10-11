$ErrorActionPreference = 'Stop'

$verifier = Join-Path $PSScriptRoot 'verify_runtime_bundle.ps1'
$scratch = Join-Path ([IO.Path]::GetTempPath()) ('codex-runtime-bundle-test-' + [guid]::NewGuid().ToString('N'))
$runtimeDir = Join-Path $scratch 'runtime'
$toolsDir = Join-Path $scratch 'tools'

function Invoke-Verifier {
    $shell = (Get-Process -Id $PID).Path
    $output = & $shell -NoProfile -NonInteractive -File $verifier `
        -RuntimeDir $runtimeDir -ToolsDir $toolsDir 2>&1
    $exitCode = $LASTEXITCODE
    # Expected-negative child runs must not leak their native exit code into the
    # GitHub Actions pwsh step after this test has asserted the failure itself.
    $global:LASTEXITCODE = 0
    return [PSCustomObject]@{
        ExitCode = $exitCode
        Output = ($output -join "`n")
    }
}

try {
    New-Item -ItemType Directory -Force -Path $runtimeDir, $toolsDir | Out-Null
    Set-Content -LiteralPath (Join-Path $runtimeDir 'helper.ps1') -Value 'Write-Host ok' -NoNewline
    Set-Content -LiteralPath (Join-Path $runtimeDir 'Launch.cmd') -Value '@echo off' -NoNewline
    Copy-Item -LiteralPath (Join-Path $runtimeDir 'helper.ps1') -Destination $toolsDir
    Copy-Item -LiteralPath (Join-Path $runtimeDir 'Launch.cmd') -Destination $toolsDir

    $pass = Invoke-Verifier
    if ($pass.ExitCode -ne 0) { throw "Expected identical bundle to pass: $($pass.Output)" }

    Set-Content -LiteralPath (Join-Path $toolsDir 'helper.ps1') -Value 'Write-Host stale' -NoNewline
    $mismatch = Invoke-Verifier
    if ($mismatch.ExitCode -eq 0 -or $mismatch.Output -notmatch 'hash mismatch') {
        throw "Expected changed helper to fail with hash mismatch: $($mismatch.Output)"
    }

    Copy-Item -LiteralPath (Join-Path $runtimeDir 'helper.ps1') -Destination $toolsDir -Force
    Set-Content -LiteralPath (Join-Path $toolsDir 'obsolete.ps1') -Value 'Write-Host stale' -NoNewline
    $extra = Invoke-Verifier
    if ($extra.ExitCode -eq 0 -or $extra.Output -notmatch 'package-only PowerShell') {
        throw "Expected package-only helper to fail: $($extra.Output)"
    }

    Remove-Item -LiteralPath (Join-Path $toolsDir 'obsolete.ps1') -Force
    Remove-Item -LiteralPath (Join-Path $toolsDir 'Launch.cmd') -Force
    $missing = Invoke-Verifier
    if ($missing.ExitCode -eq 0 -or $missing.Output -notmatch 'missing runtime file') {
        throw "Expected missing runtime file to fail: $($missing.Output)"
    }

    Write-Host 'PASS: runtime bundle verifier accepts exact copies and rejects mismatch, extra PS1 and missing files.'
} finally {
    if (Test-Path -LiteralPath $scratch) {
        Remove-Item -LiteralPath $scratch -Recurse -Force
    }
}
