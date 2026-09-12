# Repair-CodexSharedAppTools.ps1
#
# Why this exists
# ---------------
# Codex Desktop creates the app-tools named pipe in Electron's main process and hands
# the name to the app-server only through a `-c mcp_servers.codex_app=...` override on
# the app-server it spawns itself. When Launch-Codex.ps1 pre-spawns the shared
# `--listen` sidecar, that handoff never happens, the app-tools MCP cannot start, and
# every codex_app tool returns the sidecar's `unsupported call`.
#
# Measured on 10.11.1.1 with the shared sidecar running: the app-tools pipe does
# exist, and registering it is enough. After writing the discovered pipe into
# config.toml and reloading user config over the sidecar's own WebSocket,
# `mcpServerStatus/list` reports codex_app with 27 tools including
# `automation_update`, and a real tool call returns isError=false.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File Repair-CodexSharedAppTools.ps1
#   ... -Quiet                  # for launcher background use, prints only the verdict line
#   ... -WsUrl ws://127.0.0.1:24567
#   ... -SkipApply              # diagnose only: report the pipe, change nothing

[CmdletBinding()]
param(
    [string]$InstallDir,
    [string]$CodexHome,
    [string]$WsUrl,
    [switch]$Quiet,
    [switch]$SkipApply
)

$ErrorActionPreference = 'Stop'

function Write-Log {
    param([string]$Message)
    if (-not $Quiet) { Write-Host $Message }
}

if (-not $InstallDir) { $InstallDir = Join-Path $env:LOCALAPPDATA 'CodexFromGithub' }
if (-not $CodexHome)   { $CodexHome = Join-Path $env:USERPROFILE '.codex' }

$finder = Join-Path $PSScriptRoot 'Find-CodexAppToolsPipe.ps1'
if (-not (Test-Path -LiteralPath $finder)) {
    Write-Host "SKIP: finder script missing at $finder"
    return
}

# The pipe only exists once Electron has booted, so give it a chance to appear.
$found = $null
$deadline = (Get-Date).AddSeconds(60)
while ((Get-Date) -lt $deadline) {
    $scan = & $finder -TimeoutSec 5 -Quiet
    if ($scan.guard -eq 'private-sidecar-live') {
        Write-Host 'SKIP: app-server is Electron-owned, app-tools already wired by -c override'
        return
    }
    if ($scan.pipe) { $found = [string]$scan.pipe; break }
    # A private Electron-owned app-server already has the tools; nothing to repair.
    $private = @(Get-CimInstance Win32_Process -Filter "Name='codex.exe'" -EA SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match '\bapp-server\b' -and
                       $_.CommandLine -notmatch '--listen\s+ws://' })
    if ($private.Count -gt 0) {
        Write-Host 'SKIP: app-server is Electron-owned, app-tools already wired by -c override'
        return
    }
    Write-Log 'waiting for the app-tools pipe...'
    Start-Sleep -Seconds 4
}

if (-not $found) {
    Write-Host 'FAIL: no app-tools pipe found while a shared app-server was running.'
    Write-Host 'The two modes are mutually exclusive on this build; do not retry blindly.'
    return
}

Write-Log "app-tools pipe: $found"

if ($SkipApply) { return }

$applied = & $finder -TimeoutSec 5 -Quiet -Apply
if (-not $applied.applied) {
    Write-Host 'FAIL: -Apply did not report a config write.'
    return
}
if ($applied.unchanged) {
    Write-Log 'config.toml already carried the live pipe; no write, no backup.'
} else {
    Write-Log "config.toml updated with the live pipe (backup $($applied.backup))"
}

if (-not $WsUrl) {
    $stateFile = Join-Path $CodexHome 'desktop-shared-app-server.json'
    if (Test-Path -LiteralPath $stateFile) {
        try {
            $state = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
            if ($state.ws_url) { $WsUrl = [string]$state.ws_url }
        } catch { Write-Log "state file unreadable: $($_.Exception.Message)" }
    }
}
if (-not $WsUrl) { $WsUrl = 'ws://127.0.0.1:24567' }

# Reloading user config makes the running sidecar rebuild its MCP registry, which is
# what actually starts the app-tools server. Threads that already resumed keep their
# old snapshot, so a fresh thread or a new turn is needed to see the tool.
$socket = [System.Net.WebSockets.ClientWebSocket]::new()
$cts = [System.Threading.CancellationTokenSource]::new([TimeSpan]::FromSeconds(25))
$relay = $null
try {
    $uri = [System.Uri]$WsUrl
    $socket.ConnectAsync($uri, $cts.Token).Wait()
    if ($socket.State -ne 'Open') { throw "websocket connect ended in state $($socket.State)" }

    function Send-Json {
        param($Payload)
        $bytes = [Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject $Payload -Compress -Depth 12))
        $segment = [ArraySegment[byte]]::new($bytes)
        $socket.SendAsync($segment, [System.Net.WebSockets.WebSocketMessageType]::Text,
            $true, $cts.Token).Wait()
    }
    function Receive-Line {
        $buffer = [byte[]]::new(65536)
        $builder = [Text.StringBuilder]::new()
        do {
            $segment = [ArraySegment[byte]]::new($buffer)
            $task = $socket.ReceiveAsync($segment, $cts.Token)
            $task.Wait()
            $result = $task.Result
            [void]$builder.Append([Text.Encoding]::UTF8.GetString($buffer, 0, $result.Count))
            if ($result.MessageType -eq [System.Net.WebSockets.WebSocketMessageType]::Close -or
                $result.Count -eq 0) { break }
        } while (-not $result.EndOfMessage)
        return $builder.ToString()
    }

    Send-Json @{ jsonrpc = '2.0'; id = 1; method = 'initialize'; params = @{
        protocolVersion = '2025-03-26'; capabilities = @{}
        clientInfo = @{ name = 'codex-shared-app-tools-repair'; version = '0.1' } } }
    do { $line = Receive-Line } while ((-not $line) -or ($line -notmatch '"id":1\b'))

    Send-Json @{ jsonrpc = '2.0'; method = 'notifications/initialized'; params = @{} }

    Send-Json @{ jsonrpc = '2.0'; id = 2; method = 'config/batchWrite'; params = @{
        edits = @(); reloadUserConfig = $true } }
    $deadlineMs = (Get-Date).AddSeconds(20)
    $wrote = $null
    while ((Get-Date) -lt $deadlineMs) {
        $line = Receive-Line
        if ($line -and $line -match '"id":2\b') { $wrote = $line; break }
    }
    if (-not $wrote) {
        Write-Host 'FAIL: no config/batchWrite response from the sidecar.'
        return
    }
    Write-Log "reload reply: $($wrote.Substring(0, [Math]::Min(200, $wrote.Length)))"

    Send-Json @{ jsonrpc = '2.0'; id = 3; method = 'mcpServerStatus/list'; params = @{} }
    $deadlineMs = (Get-Date).AddSeconds(30)
    $status = $null
    while ((Get-Date) -lt $deadlineMs) {
        $line = Receive-Line
        if ($line -and $line -match '"id":3\b') { $status = $line; break }
    }
    if ($status -and $status -match 'codex_app') {
        Write-Host 'OK: codex_app is present in the shared sidecar MCP registry.'
        Write-Host 'Open a new thread, or send a new turn, to pick up the tool list.'
    } else {
        Write-Host 'WARN: reload accepted but codex_app is not in the registry yet.'
        if ($status) { Write-Host ($status.Substring(0, [Math]::Min(400, $status.Length))) }
    }
} catch {
    Write-Host "FAIL: could not talk to the shared app-server at $WsUrl"
    Write-Host "      $($_.Exception.Message)"
} finally {
    if ($relay) { $relay.Dispose() }
    $socket.Dispose()
    $cts.Dispose()
}
