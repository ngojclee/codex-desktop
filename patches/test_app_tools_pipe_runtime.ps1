$ErrorActionPreference = 'Stop'

function Assert-True {
    param(
        [Parameter(Mandatory=$true)][bool]$Condition,
        [Parameter(Mandatory=$true)][string]$Message
    )

    if (-not $Condition) { throw $Message }
}

$root = Join-Path $env:TEMP ("codex-app-tools-pipe-test-" + [guid]::NewGuid().Guid)
$codexHome = Join-Path $root '.codex'
$installDir = Join-Path $root 'install'
$pluginDir = Join-Path $installDir 'resources\plugins\openai-bundled\plugins\codex-app-tools\0.1.3'
$config = Join-Path $codexHome 'config.toml'
$script = Join-Path $PSScriptRoot '..\runtime\Ensure-Codex-AppToolsMcp.ps1'

New-Item -ItemType Directory -Force -Path $pluginDir, $codexHome | Out-Null

try {
    # A tiny fixture is enough: the helper only needs the stable Desktop
    # capability literal to know that removing the old static workaround is
    # safe for this install.
    [IO.File]::WriteAllText(
        (Join-Path $installDir 'resources\app.asar'),
        'CODEX_APP_TOOLS_PIPE_PATH',
        [Text.UTF8Encoding]::new($false)
    )
    [IO.File]::WriteAllText(
        (Join-Path $pluginDir 'desktop-mcp.json'),
        '{"mcpServers":{"codex_app":{"command":"node","args":["server.mjs"],"env_vars":["CODEX_APP_TOOLS_PIPE_PATH"]}}}',
        [Text.UTF8Encoding]::new($false)
    )

    $text = @'
model = "gpt-5.6-sol"

# Workaround for upstream bug
# desktop injects mcp_servers.codex_app with an unresolvable transport;
# an explicit command-based block here overrides the injected one.
[mcp_servers.codex_app]
command = "cmd.exe"
args = ["/c", "launch_codex_app_tools_mcp.cmd"]
cwd = "C:/Users/example/.codex/plugins/cache/openai-bundled/codex-app-tools/0.1.3"
enabled = true

[mcp_servers.codex_app.env]
CODEX_APP_TOOLS_PIPE_PATH = "stale-static-value"

[mcp_servers.open-design]
url = "http://127.0.0.1:7460/mcp"
enabled = true
'@
    [IO.File]::WriteAllText($config, $text, [Text.UTF8Encoding]::new($false))

    $ensureOutput = & $script -CodexHome $codexHome -InstallDir $installDir -ConfigPath $config
    Write-Host "First ensure result: $($ensureOutput | ConvertTo-Json -Depth 6 -Compress)"
    Assert-True ($?) 'First ensure run should exit successfully.'

    $after = [IO.File]::ReadAllText($config)
    Assert-True ($after -match '\[mcp_servers\.codex_app\]') `
        'A valid static codex_app transport must be kept for legacy threads.'
    Assert-True ($after -match 'command = "cmd\.exe"') `
        'Kept codex_app block must retain its command transport.'
    Assert-True ($after -match 'cwd = "C:/Users/example/\.codex/plugins/cache/openai-bundled/codex-app-tools/0\.1\.3"') `
        'Kept codex_app block must retain its cwd.'
    Assert-True ($after -match '\[mcp_servers\.open-design\]') `
        'Unrelated MCP configuration must be preserved.'
    Assert-True (@(Get-ChildItem -LiteralPath $codexHome -Filter 'config.toml.bak-before-codex-app-pipe-*').Count -eq 0) `
        'Keeping a valid legacy transport must not create a rewrite backup.'

    $mirror = Join-Path $pluginDir '.mcp.json'
    Assert-True (Test-Path -LiteralPath $mirror) 'Sidecar mirror should be created.'
    $mirrorJson = Get-Content -LiteralPath $mirror -Raw | ConvertFrom-Json
    Assert-True ($mirrorJson.mcpServers.codex_app.enabled -eq $false) `
        'The sidecar mirror must stay disabled to avoid a duplicate server.'
    Assert-True (@($mirrorJson.mcpServers.codex_app.env_vars) -contains 'CODEX_APP_TOOLS_PIPE_PATH') `
        'The mirror must retain the dynamic pipe environment declaration.'

    # CI checks out scripts as CRLF, and real Windows configs may be CRLF too.
    # Lock the .NET multiline-anchor behavior that made the old `$` pattern
    # silently report `absent` on those files.
    $crlfConfig = Join-Path $root 'crlf-config.toml'
    $crlfText = $text -replace "`r?`n", "`r`n"
    [IO.File]::WriteAllText($crlfConfig, $crlfText, [Text.UTF8Encoding]::new($false))
    & $script -CodexHome $codexHome -InstallDir $installDir -ConfigPath $crlfConfig -Quiet
    Assert-True ($?) 'CRLF config ensure run should exit successfully.'
    $crlfAfter = [IO.File]::ReadAllText($crlfConfig)
    Assert-True ($crlfAfter -match '\[mcp_servers\.codex_app\]') `
        'CRLF config must also keep a valid legacy codex_app transport.'
    Assert-True ($crlfAfter -match '\[mcp_servers\.open-design\]') `
        'CRLF cleanup must preserve unrelated MCP configuration.'

    & $script -CodexHome $codexHome -InstallDir $installDir -ConfigPath $config -Quiet
    Assert-True ($?) 'Second ensure run should exit successfully.'
    Assert-True (@(Get-ChildItem -LiteralPath $codexHome -Filter 'config.toml.bak-before-codex-app-pipe-*').Count -eq 0) `
        'Idempotent rerun must not create a backup for an unchanged valid block.'

    # A malformed/partial static definition is still unsafe and must be
    # removed, preserving the original cleanup contract for new installs.
    $invalidConfig = Join-Path $root 'invalid-config.toml'
    [IO.File]::WriteAllText(
        $invalidConfig,
        "[mcp_servers.codex_app]`ncommand = `"cmd.exe`"`n`n[mcp_servers.open-design]`nurl = `"http://127.0.0.1:7460/mcp`"`n",
        [Text.UTF8Encoding]::new($false)
    )
    & $script -CodexHome $codexHome -InstallDir $installDir -ConfigPath $invalidConfig -Quiet
    Assert-True ($?) 'Invalid transport cleanup run should exit successfully.'
    $invalidAfter = [IO.File]::ReadAllText($invalidConfig)
    Assert-True ($invalidAfter -notmatch '\[mcp_servers\.codex_app(?:\.[^\]]+)?\]') `
        'An incomplete codex_app block must still be removed.'
    Assert-True ($invalidAfter -match '\[mcp_servers\.open-design\]') `
        'Invalid transport cleanup must preserve unrelated MCP configuration.'

    # Capability gating: an older/non-pipe-aware bundle must not lose the
    # legacy workaround merely because the helper was updated.
    $legacyInstall = Join-Path $root 'legacy-install'
    New-Item -ItemType Directory -Force -Path (Join-Path $legacyInstall 'resources') | Out-Null
    $legacyConfig = Join-Path $root 'legacy-config.toml'
    [IO.File]::WriteAllText(
        $legacyConfig,
        "[mcp_servers.codex_app]`ncommand = `"node`"`n",
        [Text.UTF8Encoding]::new($false)
    )
    & $script -CodexHome $codexHome -InstallDir $legacyInstall -ConfigPath $legacyConfig -Quiet
    $legacyAfter = [IO.File]::ReadAllText($legacyConfig)
    Assert-True ($legacyAfter -match '\[mcp_servers\.codex_app\]') `
        'Legacy bundle config must remain untouched without the dynamic pipe marker.'

    Write-Host 'Codex app-tools pipe runtime tests passed.'
} finally {
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}
