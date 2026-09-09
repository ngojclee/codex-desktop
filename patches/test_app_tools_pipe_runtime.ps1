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

    & $script -CodexHome $codexHome -InstallDir $installDir -ConfigPath $config -Quiet
    Assert-True ($?) 'First ensure run should exit successfully.'

    $after = [IO.File]::ReadAllText($config)
    if ($after -match '\[mcp_servers\.codex_app(?:\.[^\]]+)?\]') {
        Write-Host "Config after first ensure run:"
        Write-Host $after
        throw 'Static codex_app parent and child tables must be removed.'
    }
    Assert-True ($after -match '\[mcp_servers\.open-design\]') `
        'Unrelated MCP configuration must be preserved.'
    Assert-True (@(Get-ChildItem -LiteralPath $codexHome -Filter 'config.toml.bak-before-codex-app-pipe-*').Count -eq 1) `
        'First run should create exactly one config backup.'

    $mirror = Join-Path $pluginDir '.mcp.json'
    Assert-True (Test-Path -LiteralPath $mirror) 'Sidecar mirror should be created.'
    $mirrorJson = Get-Content -LiteralPath $mirror -Raw | ConvertFrom-Json
    Assert-True ($mirrorJson.mcpServers.codex_app.enabled -eq $false) `
        'The sidecar mirror must stay disabled to avoid a duplicate server.'
    Assert-True (@($mirrorJson.mcpServers.codex_app.env_vars) -contains 'CODEX_APP_TOOLS_PIPE_PATH') `
        'The mirror must retain the dynamic pipe environment declaration.'

    & $script -CodexHome $codexHome -InstallDir $installDir -ConfigPath $config -Quiet
    Assert-True ($?) 'Second ensure run should exit successfully.'
    Assert-True (@(Get-ChildItem -LiteralPath $codexHome -Filter 'config.toml.bak-before-codex-app-pipe-*').Count -eq 1) `
        'Idempotent rerun must not create another backup.'

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
