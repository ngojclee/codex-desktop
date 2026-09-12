$ErrorActionPreference = 'Stop'

function Assert-True {
    param(
        [Parameter(Mandatory=$true)][bool]$Condition,
        [Parameter(Mandatory=$true)][string]$Message
    )

    if (-not $Condition) { throw $Message }
}

# Shared by the LF and CRLF fixtures: a preserved legacy transport must resolve,
# must not be spawnable, and must not carry a dead pipe path. Every pattern here
# allows an optional `\r`, because a `$` anchor alone silently stops matching on
# CRLF configs and let an enabled server through in CI once already.
function Assert-DisabledLegacyTransport {
    param(
        [Parameter(Mandatory=$true)][string]$Content,
        [Parameter(Mandatory=$true)][string]$Label
    )

    $section = [regex]::Match(
        $Content,
        '(?ms)^[ \t]*\[mcp_servers\.codex_app\][ \t]*\r?\n.*?(?=^[ \t]*\[mcp_servers\.(?!codex_app))').Value
    Assert-True ($section.Length -gt 0) "$Label : codex_app section must be present."
    Assert-True ($section -match '(?m)^[ \t]*enabled[ \t]*=[ \t]*false[ \t]*\r?$') `
        "$Label : kept codex_app transport must be disabled so Desktop owns the pipe server."
    Assert-True ($section -notmatch 'CODEX_APP_TOOLS_PIPE_PATH[ \t]*=') `
        "$Label : a stale static CODEX_APP_TOOLS_PIPE_PATH must never survive normalization."
    Assert-True ($Content -match '(?m)^[ \t]*enabled[ \t]*=[ \t]*true[ \t]*\r?$') `
        "$Label : normalization must not disable unrelated MCP servers such as open-design."
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
    Assert-DisabledLegacyTransport -Content $after -Label 'LF fixture'
    $firstRunBackups = @(
        Get-ChildItem -LiteralPath $codexHome -Filter 'config.toml.bak-before-codex-app-pipe-*'
    )
    Assert-True ($firstRunBackups.Count -eq 1) `
        'Disabling a kept legacy transport must back up the config exactly once.'

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
    Assert-DisabledLegacyTransport -Content $crlfAfter -Label 'CRLF fixture'

    & $script -CodexHome $codexHome -InstallDir $installDir -ConfigPath $config -Quiet
    Assert-True ($?) 'Second ensure run should exit successfully.'
    Assert-True (@(Get-ChildItem -LiteralPath $codexHome -Filter 'config.toml.bak-before-codex-app-pipe-*').Count -eq 1) `
        'Idempotent rerun must not rewrite an already-disabled legacy transport.'

    # A block that carries the app-tools pipe is the shared-mode repair written by
    # Repair-CodexSharedAppTools.ps1. Keeping it enabled is the only way app-tools can
    # register while Launch-Codex owns the sidecar, so the disable rule must not touch
    # it and a rerun must not rewrite or back it up.
    $pipeConfig = Join-Path $root 'pipe-config.toml'
    $pipeText = @(
        'model = "gpt-5.6-sol"'
        ''
        '[mcp_servers.codex_app]'
        'command = "cmd.exe"'
        "args = ['/d', '/s', '/c', 'call', './scripts/launch_codex_app_tools_mcp.cmd', './server.mjs']"
        'cwd = "C:/Users/example/.codex/plugins/cache/openai-bundled/codex-app-tools"'
        'enabled = true'
        ''
        '[mcp_servers.codex_app.env]'
        'CODEX_APP_TOOLS_PIPE_PATH = "pipe-placeholder-value"'
        'CODEX_MCP_NODE_PATH = "C:/example/resources/cua_node/bin/node.exe"'
        ''
        '[mcp_servers.open-design]'
        'url = "http://127.0.0.1:7460/mcp"'
        'enabled = true'
    ) -join "`r`n"
    [IO.File]::WriteAllText($pipeConfig, $pipeText, [Text.UTF8Encoding]::new($false))
    & $script -CodexHome $codexHome -InstallDir $installDir -ConfigPath $pipeConfig -Quiet
    Assert-True ($?) 'Piped codex_app config ensure run should exit successfully.'
    Assert-True ([IO.File]::ReadAllText($pipeConfig) -eq $pipeText) `
        'A block carrying the app-tools pipe must survive untouched, or the shared-mode repair is undone.'
    # Assert on the file rather than the returned status object: an untouched
    # `enabled = true` is the whole contract, and the result shape is not stable
    # between Windows PowerShell 5.1 and pwsh 7.
    $pipeAfter = [IO.File]::ReadAllText($pipeConfig)
    $pipeSection = [regex]::Match(
        $pipeAfter,
        '(?ms)^[ \t]*\[mcp_servers\.codex_app\][ \t]*\r?\n.*?(?=^[ \t]*\[mcp_servers\.(?!codex_app))').Value
    Assert-True ($pipeSection -match '(?m)^[ \t]*enabled[ \t]*=[ \t]*true[ \t]*\r?$') `
        'A block carrying the app-tools pipe must stay enabled so the sidecar can start it.'
    Assert-True ($pipeSection -match 'CODEX_APP_TOOLS_PIPE_PATH[ \t]*=') `
        'The registered app-tools pipe must not be stripped.'
    Assert-True (@(Get-ChildItem -LiteralPath $root -Filter 'pipe-config.toml.bak-*').Count -eq 0) `
        'Leaving the repaired block alone must not create a backup.'

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
