# Find-CodexAppToolsPipe.ps1
#
# Why this exists
# ---------------
# Codex Desktop hands the app-tools MCP server its per-session named pipe through a
# `-c mcp_servers.codex_app={...}` override on the app-server *it* spawns. When
# Launch-Codex.ps1 pre-spawns the shared `--listen` sidecar instead, that override
# never reaches the sidecar, the app-tools MCP cannot start, `codex_app` tools are
# never registered, and every call fails with the sidecar's `unsupported call`.
#
# The pipe name is not guessable, and Electron publishes it under the shared
# `codex-browser-use-*` namespace, so a naive enumeration dismisses it. This script
# identifies which candidate pipe really is the app-tools host by running the
# bundle's own `server.mjs` against each one and asking it for its tool list. Reusing
# their client means the probe tracks upstream protocol changes for free.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File Find-CodexAppToolsPipe.ps1
#   ... -Pipes '\\.\pipe\codex-ipc'      # probe explicit names instead of scanning
#   ... -Apply                           # write the discovered pipe into config.toml
#
# Callers should read the emitted object, not the console text: Write-Host goes to the
# information stream, so grepping a child's output silently loses every verdict line.
#
# -Apply only rewrites config.toml. The running sidecar reads it on next start, so
# follow it with runtime\refresh-codex-app-server.ps1, or restart Codex.

[CmdletBinding()]
param(
    [string]$InstallDir,
    [string]$CodexHome,
    [string[]]$Pipes,
    [int]$TimeoutSec = 8,
    [switch]$Apply,
    [switch]$Quiet,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

function Write-Note {
    param([string]$Message)
    if (-not $Quiet) { Write-Host $Message }
}

if (-not $InstallDir) { $InstallDir = Join-Path $env:LOCALAPPDATA 'CodexFromGithub' }
if (-not $CodexHome)   { $CodexHome = Join-Path $env:USERPROFILE '.codex' }

$nodeExe = Join-Path $InstallDir 'resources\cua_node\bin\node.exe'
if (-not (Test-Path -LiteralPath $nodeExe)) {
    throw "Bundled node runtime not found: $nodeExe"
}

# Electron's own override points at the bundle copy, not the user plugin cache, so
# probe and later configure the same path the app uses.
$pluginDir = Join-Path $InstallDir 'resources\plugins\openai-bundled\plugins\codex-app-tools'
$serverMjs = Join-Path $pluginDir 'server.mjs'
if (-not (Test-Path -LiteralPath $serverMjs)) {
    throw "App-tools server not found: $serverMjs"
}

# A private (Electron-owned) app-server already has an attached client on the real
# pipe. Attaching our probe would steal or disturb that session's app-tools lane, so
# refuse unless the caller insists.
function Get-PrivateSidecar {
    @(Get-CimInstance Win32_Process -Filter "Name='codex.exe'" -EA SilentlyContinue |
        Where-Object { $_.CommandLine -and
            $_.CommandLine -match '\bapp-server\b' -and
            $_.CommandLine -notmatch '--listen\s+ws://' })
}

$private = Get-PrivateSidecar
if ($private.Count -gt 0 -and -not $Force) {
    $pids = ($private | ForEach-Object { $_.ProcessId }) -join ','
    Write-Note ("Refusing to probe: {0} Electron-owned app-server(s) live (PID {1})." -f $private.Count, $pids)
    Write-Note 'Pass -Force to probe anyway, which can disturb the active app-tools lane,'
    Write-Note 'or pass -Pipes with names that are definitely not the app-tools pipe.'
    return [PSCustomObject]@{ guard = 'private-sidecar-live'; privatePids = $pids; pipe = $null }
}

if (-not $Pipes) {
    $Pipes = [System.IO.Directory]::GetFiles('\\.\pipe\') |
        Where-Object { $_ -match 'codex-browser-use|codex-ipc|app-tools' } |
        Sort-Object
}
if ($Pipes.Count -eq 0) {
    Write-Note 'No candidate pipes found.'
    return [PSCustomObject]@{ guard = $null; pipe = $null; candidates = @() }
}

$probeSource = @'
import { spawn } from 'node:child_process';
const [nodeExe, serverMjs, pipePath, timeoutMsArg] = process.argv.slice(2);
const timeoutMs = Number(timeoutMsArg || 8000);
const child = spawn(nodeExe, [serverMjs], {
  env: { ...process.env, CODEX_APP_TOOLS_PIPE_PATH: pipePath },
  stdio: ['pipe', 'pipe', 'pipe'],
});
let buf = '';
let nextId = 0;
const pending = new Map();
const send = (method, params) => {
  const id = ++nextId;
  child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, ...(params ? { params } : {}) }) + '\n');
  return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
};
child.stdout.on('data', (chunk) => {
  buf += chunk.toString();
  for (;;) {
    const nl = buf.indexOf('\n');
    if (nl < 0) break;
    const line = buf.slice(0, nl).trim();
    buf = buf.slice(nl + 1);
    if (!line) continue;
    let msg;
    try { msg = JSON.parse(line); } catch { continue; }
    if (msg.id != null && pending.has(msg.id)) {
      const waiter = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) waiter.reject(new Error(JSON.stringify(msg.error)));
      else waiter.resolve(msg.result);
    }
  }
});
const bail = (payload, code) => {
  console.log(JSON.stringify(payload));
  child.kill();
  process.exitCode = code;
  process.exit(code);
};
const timer = setTimeout(() => bail({ pipe: pipePath, ok: false, reason: 'probe timeout' }), timeoutMs);
try {
  await send('initialize', {
    protocolVersion: '2025-03-26',
    capabilities: {},
    clientInfo: { name: 'codex-app-tools-pipe-probe', version: '0.1' },
  });
  child.stdin.write(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) + '\n');
  const listed = await send('tools/list', {});
  const names = (listed.tools || []).map((tool) => tool.name);
  clearTimeout(timer);
  console.log(JSON.stringify({
    pipe: pipePath,
    ok: true,
    toolCount: names.length,
    isAppTools: names.includes('automation_update'),
    tools: names.slice(0, 16),
  }));
  child.kill();
  process.exit(0);
} catch (error) {
  clearTimeout(timer);
  bail({ pipe: pipePath, ok: false, reason: String((error && error.message) || error).slice(0, 300) }, 3);
}
'@

$probeFile = Join-Path $env:TEMP ("codex-app-tools-pipe-probe-" + [guid]::NewGuid().Guid + ".mjs")
[IO.File]::WriteAllText($probeFile, $probeSource, [Text.UTF8Encoding]::new($false))

$results = @()
try {
    foreach ($pipe in $Pipes) {
        $raw = & $nodeExe $probeFile $nodeExe $serverMjs $pipe ($TimeoutSec * 1000) 2>$null |
            Out-String
        $parsed = $null
        if ($raw.Trim()) {
            try { $parsed = $raw.Trim() | ConvertFrom-Json } catch { }
        }
        if ($null -eq $parsed) {
            $parsed = [PSCustomObject]@{ pipe = $pipe; ok = $false; reason = 'no probe output' }
        }
        $results += $parsed
        $verdict = if ($parsed.ok -and $parsed.isAppTools) { 'APP-TOOLS' }
                   elseif ($parsed.ok) { 'mcp-but-not-app-tools' }
                   else { "no ($($parsed.reason))" }
        Write-Note ("{0,-70} {1}" -f $pipe, $verdict)
    }
} finally {
    Remove-Item -LiteralPath $probeFile -Force -EA SilentlyContinue
}

$winner = @($results | Where-Object { $_.ok -and $_.isAppTools }) | Select-Object -First 1
if ($null -eq $winner) {
    Write-Note 'No candidate served automation_update.'
    Write-Note 'In shared --listen mode this means Electron never created the app-tools pipe,'
    Write-Note 'so no config change can recover it and the app-server has to be Electron-owned.'
    return [PSCustomObject]@{
        guard = $null
        pipe = $null
        candidates = @($results | ForEach-Object { [PSCustomObject]@{
            pipe = $_.pipe; ok = [bool]$_.ok; reason = $_.reason } })
    }
}

Write-Note ("Discovered app-tools pipe: {0} ({1} tools)" -f $winner.pipe, $winner.toolCount)

if (-not $Apply) {
    Write-Note 'Dry run only. Re-run with -Apply to write it into config.toml.'
    return [PSCustomObject]@{ guard = $null; pipe = $winner.pipe; toolCount = $winner.toolCount;
        applied = $false; tools = $winner.tools }
}

$configPath = Join-Path $CodexHome 'config.toml'
$text = [IO.File]::ReadAllText($configPath)
$header = '[mcp_servers.codex_app]'
# TOML literal (single-quoted) strings do no escape processing, so Windows paths go
# in verbatim; doubling backslashes here would corrupt them.
$block = @(
    $header
    'command = "cmd.exe"'
    "args = ['/d', '/s', '/c', 'call', './scripts/launch_codex_app_tools_mcp.cmd', './server.mjs']"
    "cwd = '$pluginDir'"
    'enabled = true'
    ''
    '[mcp_servers.codex_app.env]'
    "CODEX_APP_TOOLS_PIPE_PATH = '$($winner.pipe)'"
    "CODEX_MCP_NODE_PATH = '$nodeExe'"
    ''
) -join "`r`n"

if ($text -match '(?m)^\[mcp_servers\.codex_app\]') {
    $headerRegex = [regex]::new('(?m)^\[[^\r\n]+\][ \t]*(?:#.*)?(?=\r?\n|$)')
    $start = [regex]::Match($text, '(?m)^\[mcp_servers\.codex_app\]')
    $next = $headerRegex.Match($text, $start.Index + $start.Length)
    while ($next.Success -and $next.Value.Trim() -match '^\[mcp_servers\.codex_app(?:\.[^\]]+)?\]') {
        $next = $headerRegex.Match($text, $next.Index + $next.Length)
    }
    $end = if ($next.Success) { $next.Index } else { $text.Length }
    $updated = $text.Substring(0, $start.Index) + $block + $text.Substring($end)
} else {
    $updated = $text.TrimEnd("`r", "`n") + "`r`n`r`n" + $block
}

# The launcher may call this on every boot and the pipe name changes per Electron
# session, so compare first: backing up identical bytes would leave one stale config
# backup per start.
if ($updated -eq $text) {
    Write-Note 'config.toml already carries this pipe; nothing to write.'
    return [PSCustomObject]@{ guard = $null; pipe = $winner.pipe; toolCount = $winner.toolCount;
        applied = $true; unchanged = $true; configPath = $configPath; backup = $null;
        tools = $winner.tools }
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = "$configPath.bak-before-app-tools-pipe-$stamp"
Copy-Item -LiteralPath $configPath -Destination $backup -Force
[IO.File]::WriteAllText($configPath, $updated, [Text.UTF8Encoding]::new($false))
Write-Note "Wrote $configPath (backup $backup)"
Write-Note 'Restart the sidecar, or reload user config, so it picks up the env.'
return [PSCustomObject]@{ guard = $null; pipe = $winner.pipe; toolCount = $winner.toolCount;
    applied = $true; unchanged = $false; configPath = $configPath; backup = $backup;
    tools = $winner.tools }
