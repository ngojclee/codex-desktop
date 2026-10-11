$ErrorActionPreference = 'Stop'

$ensureScript = Join-Path (Split-Path $PSScriptRoot -Parent) 'runtime\Ensure-Codex-GoogleMcp.ps1'
$scratch = Join-Path ([IO.Path]::GetTempPath()) ('codex-google-mcp-test-' + [guid]::NewGuid().ToString('N'))
$serverNames = @('google-drive', 'google-sheets', 'google-docs', 'google-slides', 'google-drive-comments')

function Invoke-Ensure {
    param([Parameter(Mandatory = $true)][string]$CodexHome)

    $json = & $ensureScript -CodexHome $CodexHome -Endpoint 'http://127.0.0.1:3110/mcp' `
        -StartupTimeoutSec 45 -BackupKeep 0 -Json
    return ($json | ConvertFrom-Json)
}

try {
    $stdioHome = Join-Path $scratch 'stdio'
    New-Item -ItemType Directory -Force -Path $stdioHome | Out-Null
    $stdioConfig = Join-Path $stdioHome 'config.toml'
    $stdioSections = foreach ($name in $serverNames) {
        @"
[mcp_servers.$name]
command = "powershell.exe"
args = ["-File", "run-mcp-http-noauth.ps1", "-Url", "http://127.0.0.1:3110/mcp"]
url = "http://stale.invalid/mcp"
startup_timeout_sec = 10.0
"@
    }
    [IO.File]::WriteAllText($stdioConfig, ($stdioSections -join "`r`n"), [Text.UTF8Encoding]::new($false))

    $stdioFirst = Invoke-Ensure -CodexHome $stdioHome
    if (-not $stdioFirst.changed) { throw 'Expected stale stdio url keys to be removed.' }
    $stdioText = [IO.File]::ReadAllText($stdioConfig)
    if ([regex]::Matches($stdioText, '(?m)^\s*url\s*=').Count -ne 0) {
        throw 'stdio sections still contain an invalid url key.'
    }
    if ([regex]::Matches($stdioText, '(?m)^\s*command\s*=').Count -ne $serverNames.Count) {
        throw 'stdio command definitions were not preserved.'
    }
    if ([regex]::Matches($stdioText, '(?m)^\s*startup_timeout_sec\s*=\s*45\.0\s*$').Count -ne $serverNames.Count) {
        throw 'stdio timeout values were not normalized.'
    }
    $stdioSecond = Invoke-Ensure -CodexHome $stdioHome
    if ($stdioSecond.changed) { throw 'Second stdio ensure must be idempotent.' }

    $remoteHome = Join-Path $scratch 'remote'
    New-Item -ItemType Directory -Force -Path $remoteHome | Out-Null
    $remoteConfig = Join-Path $remoteHome 'config.toml'
    $remoteSeed = @'
[mcp_servers.google-drive]
url = "http://old.invalid/mcp"
'@
    [IO.File]::WriteAllText($remoteConfig, $remoteSeed, [Text.UTF8Encoding]::new($false))
    $remoteFirst = Invoke-Ensure -CodexHome $remoteHome
    if (-not $remoteFirst.changed) { throw 'Expected remote transport and missing sections to be updated.' }
    $remoteText = [IO.File]::ReadAllText($remoteConfig)
    if ([regex]::Matches($remoteText, '(?m)^\s*url\s*=\s*"http://127\.0\.0\.1:3110/mcp"\s*$').Count -ne $serverNames.Count) {
        throw 'Remote URL transport was not materialized for all Google MCP servers.'
    }
    $remoteSecond = Invoke-Ensure -CodexHome $remoteHome
    if ($remoteSecond.changed) { throw 'Second remote ensure must be idempotent.' }

    Write-Host 'PASS: Google MCP ensure preserves stdio command transports, removes invalid url keys, supports remote URL mode and is idempotent.'
} finally {
    if (Test-Path -LiteralPath $scratch) {
        Remove-Item -LiteralPath $scratch -Recurse -Force
    }
}
