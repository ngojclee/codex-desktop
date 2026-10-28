$ErrorActionPreference = 'Stop'

$root = Split-Path $PSScriptRoot -Parent
$ensureScript = Join-Path $root 'runtime\Ensure-Codex-GoogleMcp.ps1'
$scratch = Join-Path ([IO.Path]::GetTempPath()) ('codex-google-mcp-test-' + [guid]::NewGuid().ToString('N'))

try {
    $existingHome = Join-Path $scratch 'existing'
    New-Item -ItemType Directory -Force -Path $existingHome | Out-Null
    $configPath = Join-Path $existingHome 'config.toml'
    $seed = @'
[mcp_servers.business]
command = "business-wrapper"

[mcp_servers.google-drive]
url = "http://<LAN-HOST>:3110/mcp"

[mcp_servers.google-sheets]
command = "powershell.exe"
args = ["-File", "workspace-wrapper.ps1"]
'@
    [IO.File]::WriteAllText($configPath, $seed, [Text.UTF8Encoding]::new($false))
    $beforeHash = (Get-FileHash -LiteralPath $configPath).Hash
    $beforeWrite = (Get-Item -LiteralPath $configPath).LastWriteTimeUtc

    # A stale caller may still supply the old endpoint/options. All are no-ops.
    foreach ($iteration in 1..2) {
        $result = & $ensureScript -CodexHome $existingHome `
            -Endpoint 'http://stale.invalid/mcp' -StartupTimeoutSec 1 -BackupKeep 0 -Json |
            ConvertFrom-Json
        if ($result.changed -or -not $result.retired -or -not $result.disabled) {
            throw 'Retired bootstrap must report a disabled no-op.'
        }
    }
    & $ensureScript -CodexHome $existingHome -Quiet
    if ((Get-FileHash -LiteralPath $configPath).Hash -ne $beforeHash -or
        (Get-Item -LiteralPath $configPath).LastWriteTimeUtc -ne $beforeWrite) {
        throw 'Retired bootstrap changed existing MCP configuration.'
    }
    if (@(Get-ChildItem -LiteralPath $existingHome -Force).Count -ne 1) {
        throw 'Retired bootstrap created a backup or other artifact.'
    }

    $missingHome = Join-Path $scratch 'missing'
    $result = & $ensureScript -CodexHome $missingHome -Json | ConvertFrom-Json
    if ($result.changed -or -not $result.retired -or (Test-Path -LiteralPath $missingHome)) {
        throw 'Retired bootstrap must not create a missing home/config.'
    }

    foreach ($file in @('Launch-Codex.ps1', 'Update-Codex.ps1')) {
        $text = [IO.File]::ReadAllText((Join-Path $root "runtime\$file"))
        if ($text -match 'Ensure-GoogleMcpConfig|Ensure-Codex-GoogleMcp\.ps1') {
            throw "$file still invokes the retired Google MCP bootstrap."
        }
        $tokens = $null
        $errors = $null
        [void][Management.Automation.Language.Parser]::ParseInput($text, [ref]$tokens, [ref]$errors)
        if ($errors.Count) { throw "$file contains a PowerShell syntax error." }
    }
    Write-Host 'PASS: retired Google bootstrap is a no-op for old callers, preserves config bytes/mtime, creates nothing, and is absent from launch/update.'
} finally {
    $resolvedScratch = [IO.Path]::GetFullPath($scratch)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolvedScratch.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing test cleanup outside Temp.'
    }
    if (Test-Path -LiteralPath $resolvedScratch) {
        Remove-Item -LiteralPath $resolvedScratch -Recurse -Force
    }
}
