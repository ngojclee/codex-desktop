# codex-exec-remote.ps1
#
# Dispatch a prompt to an existing Codex thread through the shared app-server
# sidecar (the one Codex Desktop is already connected to). Replaces the
# `codex exec resume <id> "prompt"` pattern for the non-interactive
# Planner -> Worker flow — but routes through the shared WS sidecar so Desktop
# UI shows the dispatch in real time (spinner + token streaming on the target
# thread).
#
# Reads the live sidecar URL from `~/.codex/desktop-shared-app-server.json`
# (written by `Launch-Codex.ps1`).
#
# Usage:
#   .\codex-exec-remote.ps1 -ThreadId <UUID> -Prompt "Reply in 3 words"
#   "do something" | .\codex-exec-remote.ps1 -ThreadId <UUID>
#   .\codex-exec-remote.ps1 -ThreadId <UUID> -Prompt "..." -WsUrl ws://127.0.0.1:24567
#   .\codex-exec-remote.ps1 -ThreadId <UUID> -Prompt "..." -Json   # emit JSONL of every notification
#
# Exit codes:
#   0 — turn completed
#   1 — protocol / WS error
#   2 — server-reported error
#   3 — turn ended with status != 'completed' (interrupted/failed)

[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ThreadId,
    [string]$Prompt,
    [string]$WsUrl,
    [int]$TimeoutSec = 600,
    [switch]$Json,
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'

# --- Resolve WS URL ---
if (-not $WsUrl) {
    $stateFile = Join-Path $env:USERPROFILE '.codex\desktop-shared-app-server.json'
    if (-not (Test-Path -LiteralPath $stateFile)) {
        throw "No shared-sidecar state at $stateFile. Launch Codex via tools\Launch-Codex.vbs first, or pass -WsUrl."
    }
    $state = Get-Content -Raw -LiteralPath $stateFile | ConvertFrom-Json
    if (-not $state.ws_url) { throw "State file at $stateFile missing 'ws_url' field." }
    $WsUrl = $state.ws_url
}

# --- Resolve prompt (arg or stdin) ---
if (-not $Prompt) {
    if ([Console]::IsInputRedirected) {
        $Prompt = [Console]::In.ReadToEnd()
    }
}
if ([string]::IsNullOrWhiteSpace($Prompt)) {
    throw "Empty prompt. Pass -Prompt '<text>' or pipe text via stdin."
}

# --- WS plumbing ---
$ws = New-Object System.Net.WebSockets.ClientWebSocket
$cts = New-Object System.Threading.CancellationTokenSource
$cts.CancelAfter([TimeSpan]::FromSeconds($TimeoutSec))
$tok = $cts.Token

$wsUri = [System.Uri]$WsUrl
[void]$ws.ConnectAsync($wsUri, $tok).GetAwaiter().GetResult()

function Send-JsonRpc {
    param([string]$Method, $Params, $Id)
    $msg = [ordered]@{
        jsonrpc = '2.0'
        id      = $Id
        method  = $Method
        params  = $Params
    }
    $json = $msg | ConvertTo-Json -Depth 32 -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    $seg = [System.ArraySegment[byte]]::new($bytes)
    $null = $ws.SendAsync($seg, [System.Net.WebSockets.WebSocketMessageType]::Text, $true, $tok).GetAwaiter().GetResult()
    return $json
}

function Recv-Json {
    $sb = New-Object System.Text.StringBuilder
    $buf = New-Object byte[] 32768
    while ($true) {
        $seg = [System.ArraySegment[byte]]::new($buf)
        $r = $ws.ReceiveAsync($seg, $tok).GetAwaiter().GetResult()
        if ($r.MessageType -eq [System.Net.WebSockets.WebSocketMessageType]::Close) {
            return $null
        }
        [void]$sb.Append([System.Text.Encoding]::UTF8.GetString($buf, 0, $r.Count))
        if ($r.EndOfMessage) { break }
    }
    if ($sb.Length -eq 0) { return $null }
    return $sb.ToString() | ConvertFrom-Json
}

function Wait-Response([int]$Id) {
    while ($true) {
        $m = Recv-Json
        if ($null -eq $m) { throw "WS closed before response id=${Id}" }
        if ($Json) { ($m | ConvertTo-Json -Depth 32 -Compress) | Out-Host }
        if ($m.id -eq $Id) {
            if ($m.error) { throw ("JSON-RPC error on id={0}: {1}" -f $Id, $m.error.message) }
            return $m.result
        }
        # else: it's a notification arriving before our response — ignore here
    }
}

$idCounter = 0

try {
    # 1. initialize handshake (clientInfo helps the server's logs identify us)
    $idCounter++
    $null = Send-JsonRpc 'initialize' ([ordered]@{
        clientInfo = [ordered]@{
            name    = 'codex-exec-remote.ps1'
            title   = 'CLI WS wrapper'
            version = '0.1.0'
        }
    }) $idCounter
    $null = Wait-Response $idCounter

    # 2. thread/resume — tell the sidecar to load the thread so subsequent
    #    turn/start operates against it. excludeTurns=true to avoid loading
    #    full history (we only need the live socket subscription).
    $idCounter++
    $null = Send-JsonRpc 'thread/resume' ([ordered]@{
        threadId     = $ThreadId
        excludeTurns = $true
    }) $idCounter
    $null = Wait-Response $idCounter

    # 3. turn/start — submit prompt. Sidecar will broadcast notifications to
    #    every subscribed client, including Codex Desktop's renderer.
    $idCounter++
    $null = Send-JsonRpc 'turn/start' ([ordered]@{
        threadId = $ThreadId
        input    = @(
            [ordered]@{ type = 'text'; text = $Prompt }
        )
    }) $idCounter

    # 4. Stream notifications until turn/completed (or error).
    $finalText = New-Object System.Text.StringBuilder
    $turnAck = $false
    $exit = 1
    while ($true) {
        $m = Recv-Json
        if ($null -eq $m) {
            Write-Error 'WS closed unexpectedly during turn streaming'
            $exit = 1
            break
        }
        if ($Json) { ($m | ConvertTo-Json -Depth 32 -Compress) | Out-Host }

        if ($m.id -eq $idCounter -and ($m.result -or $m.error)) {
            # turn/start ACK; the actual streaming arrives via notifications
            if ($m.error) { throw "turn/start failed: $($m.error.message)" }
            $turnAck = $true
            continue
        }

        switch ($m.method) {
            'item/agentMessage/delta' {
                $delta = $m.params.delta
                if ($delta) {
                    [void]$finalText.Append($delta)
                    if (-not $Quiet) { [Console]::Out.Write($delta) }
                }
            }
            'error' {
                Write-Error "server error notification: $($m.params | ConvertTo-Json -Depth 8 -Compress)"
                $exit = 2
                break
            }
            'turn/completed' {
                $status = $m.params.turn.status
                if (-not $Quiet) {
                    if ($finalText.Length -eq 0) {
                        # Sidecar may not have streamed deltas (older protocol);
                        # extract final assistant text from turn.items.
                        foreach ($it in $m.params.turn.items) {
                            if ($it.type -eq 'agentMessage' -and $it.text) {
                                [Console]::Out.Write($it.text)
                            }
                        }
                    }
                    [Console]::Out.WriteLine('')
                }
                if ($status -eq 'completed') { $exit = 0 } else { $exit = 3 }
                break
            }
        }
        if ($exit -ne 1 -and $m.method -in @('turn/completed','error')) { break }
    }
}
finally {
    try {
        $ws.CloseAsync([System.Net.WebSockets.WebSocketCloseStatus]::NormalClosure, 'done', [Threading.CancellationToken]::None).Wait(1500) | Out-Null
    } catch {}
    $ws.Dispose()
}

exit $exit
