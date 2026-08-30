# Dispatch a prompt into an existing local Codex thread through the shared
# sidecar. The receiver plugin calls this script; it does not use Codex CLI/TUI,
# so the target Desktop UI keeps realtime streaming.

[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ThreadId,
    [string]$Prompt,
    [string]$WsUrl,
    [ValidateRange(1, 3600)][int]$TimeoutSec = 900
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($Prompt)) {
    throw 'Empty prompt.'
}

if (-not $WsUrl) {
    $stateFile = Join-Path $env:USERPROFILE '.codex\desktop-shared-app-server.json'
    if (-not (Test-Path -LiteralPath $stateFile)) {
        throw "No shared-sidecar state at $stateFile."
    }
    $state = Get-Content -Raw -LiteralPath $stateFile | ConvertFrom-Json
    if (-not $state.ws_url) { throw 'State file is missing ws_url.' }
    $WsUrl = $state.ws_url
}

$ws = [System.Net.WebSockets.ClientWebSocket]::new()
$cts = [System.Threading.CancellationTokenSource]::new()
$cts.CancelAfter([TimeSpan]::FromSeconds($TimeoutSec))
$token = $cts.Token

function Send-JsonRpc {
    param([string]$Method, $Params, [int]$Id)
    $payload = [ordered]@{
        jsonrpc = '2.0'
        id      = $Id
        method  = $Method
        params  = $Params
    } | ConvertTo-Json -Depth 32 -Compress
    $bytes = [Text.Encoding]::UTF8.GetBytes($payload)
    $null = $ws.SendAsync(
        [ArraySegment[byte]]::new($bytes),
        [System.Net.WebSockets.WebSocketMessageType]::Text,
        $true,
        $token
    ).GetAwaiter().GetResult()
}

function Receive-Json {
    $buffer = [byte[]]::new(32768)
    $builder = [Text.StringBuilder]::new()
    while ($true) {
        $segment = [ArraySegment[byte]]::new($buffer)
        $received = $ws.ReceiveAsync($segment, $token).GetAwaiter().GetResult()
        if ($received.MessageType -eq [System.Net.WebSockets.WebSocketMessageType]::Close) {
            return $null
        }
        [void]$builder.Append([Text.Encoding]::UTF8.GetString($buffer, 0, $received.Count))
        if ($received.EndOfMessage) { break }
    }
    if ($builder.Length -eq 0) { return $null }
    return $builder.ToString() | ConvertFrom-Json
}

function Wait-Response {
    param([int]$Id)
    while ($true) {
        $message = Receive-Json
        if ($null -eq $message) { throw 'WebSocket closed before response.' }
        if ($message.id -eq $Id) {
            if ($message.error) { throw $message.error.message }
            return $message.result
        }
    }
}

try {
    $ws.ConnectAsync([Uri]$WsUrl, $token).GetAwaiter().GetResult()

    Send-JsonRpc 'initialize' ([ordered]@{
        clientInfo = [ordered]@{
            name    = 'codex-desktop-relay'
            title   = 'Codex Desktop Relay'
            version = '0.2.0'
        }
        capabilities = [ordered]@{ experimentalApi = $true }
    }) 1
    $null = Wait-Response -Id 1

    Send-JsonRpc 'thread/resume' ([ordered]@{
        threadId = $ThreadId
        excludeTurns = $true
    }) 2
    $null = Wait-Response -Id 2

    Send-JsonRpc 'turn/start' ([ordered]@{
        threadId = $ThreadId
        input = @([ordered]@{ type = 'text'; text = $Prompt })
    }) 3

    $builder = [Text.StringBuilder]::new()
    while ($true) {
        $message = Receive-Json
        if ($null -eq $message) { throw 'WebSocket closed during dispatch.' }
        if ($message.id -eq 3 -and $message.error) { throw $message.error.message }
        if ($message.method -eq 'item/agentMessage/delta') {
            [void]$builder.Append([string]$message.params.delta)
        }
        if ($message.method -eq 'error') {
            throw ($message.params | ConvertTo-Json -Depth 8 -Compress)
        }
        if ($message.method -eq 'turn/completed') {
            $status = [string]$message.params.turn.status
            if ($status -ne 'completed') {
                throw "Turn ended with status $status"
            }
            if ($builder.Length -eq 0) {
                foreach ($item in $message.params.turn.items) {
                    if ($item.type -eq 'agentMessage' -and $item.text) {
                        [void]$builder.Append($item.text)
                    }
                }
            }
            [Console]::Out.Write($builder.ToString())
            return
        }
    }
}
finally {
    try {
        $null = $ws.CloseAsync(
            [System.Net.WebSockets.WebSocketCloseStatus]::NormalClosure,
            'done',
            [Threading.CancellationToken]::None
        ).Wait(1000)
    } catch {}
    $ws.Dispose()
}
