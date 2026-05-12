# Launch-Codex.ps1
#
# Lifecycle launcher for Codex Desktop (GitHub Patched) with shared sidecar.
#
# Behavior:
#   1. Pick a free port (24567..24600).
#   2. Start `codex.exe app-server --listen ws://127.0.0.1:PORT` hidden in the
#      background; capture stdout/stderr to a rotating log under %TEMP%.
#   3. Set `CODEX_APP_SERVER_WS_URL` so the Electron app uses the WS transport
#      (Patch G must already be applied to the asar).
#   4. Launch Codex.exe and wait until ALL Codex.exe processes exit.
#   5. Kill the sidecar and clear the state file.
#
# If Codex Desktop is already running (any Codex.exe process), this script
# defers to Electron's single-instance handling and just brings the existing
# window forward — without starting a second sidecar.
#
# Run via Launch-Codex.cmd (hidden console + execution policy bypass) or
# directly: `powershell -ExecutionPolicy Bypass -File Launch-Codex.ps1`.

[CmdletBinding()]
param(
    [int]$PortMin = 24567,
    [int]$PortMax = 24600,
    [int]$BindTimeoutSec = 8,
    [switch]$ShowSidecarWindow
)

$ErrorActionPreference = 'Stop'

$InstallDir   = "$env:LOCALAPPDATA\CodexFromGithub"
$SidecarExe   = Join-Path $InstallDir 'resources\codex.exe'
$DesktopExe   = Join-Path $InstallDir 'Codex.exe'
$StateFile    = Join-Path $env:USERPROFILE '.codex\desktop-shared-app-server.json'
$LogDir       = Join-Path $env:TEMP 'codex-shared'

if (-not (Test-Path -LiteralPath $SidecarExe)) { throw "Sidecar missing: $SidecarExe" }
if (-not (Test-Path -LiteralPath $DesktopExe)) { throw "Desktop missing: $DesktopExe" }

function Test-PortListen([int]$port) {
    return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -EA SilentlyContinue)
}

function Get-FreePort([int]$min, [int]$max) {
    for ($p = $min; $p -le $max; $p++) {
        # treat ANY binding on the port as in-use (covers zombie/TIME_WAIT too)
        if (-not (Get-NetTCPConnection -LocalPort $p -EA SilentlyContinue)) { return $p }
    }
    throw "No free port between $min..$max"
}

function Get-DesktopProcessCount {
    @(Get-Process -Name 'Codex' -EA SilentlyContinue | Where-Object { $_.Path -eq $DesktopExe }).Count
}

# Honor an existing live Desktop instance — Electron single-instance lock will
# bring it forward when we Start-Process again.
if ((Get-DesktopProcessCount) -gt 0) {
    Write-Host "Codex Desktop already running — focusing existing window."
    Start-Process -FilePath $DesktopExe
    return
}

# Cleanup any stale state file pointing at a dead sidecar.
if (Test-Path -LiteralPath $StateFile) {
    try {
        $stale = Get-Content -Raw -LiteralPath $StateFile | ConvertFrom-Json
        if ($stale.sidecar_pid) {
            try { Stop-Process -Id $stale.sidecar_pid -Force -EA Stop } catch {}
        }
    } catch {}
    Remove-Item -LiteralPath $StateFile -Force -EA SilentlyContinue
}

$Port = Get-FreePort -min $PortMin -max $PortMax
$WsUrl = "ws://127.0.0.1:$Port"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$ts = Get-Date -Format yyyyMMdd-HHmmss
$logOut = Join-Path $LogDir "app-server-$ts.out.log"
$logErr = Join-Path $LogDir "app-server-$ts.err.log"

if ($ShowSidecarWindow) {
    # Visible window — handy for debugging
    $inner = "`$Host.UI.RawUI.WindowTitle = 'Codex Shared Sidecar ($WsUrl)'; " +
             "Write-Host 'Sidecar — close this window to stop Codex.' -Fore Yellow; " +
             "& '$SidecarExe' app-server --listen '$WsUrl' 2>&1 | " +
             "Tee-Object -FilePath '$logOut'"
    $sidecarHost = Start-Process powershell.exe `
        -ArgumentList @('-NoExit','-NoProfile','-Command',$inner) `
        -PassThru
    # The actual codex.exe is a CHILD of the PS host. Track host for kill.
    $sidecarPid = $sidecarHost.Id
}
else {
    $sidecar = Start-Process -FilePath $SidecarExe `
        -ArgumentList 'app-server','--listen',$WsUrl `
        -RedirectStandardOutput $logOut `
        -RedirectStandardError  $logErr `
        -WindowStyle Hidden `
        -PassThru
    $sidecarPid = $sidecar.Id
}

# Wait until sidecar binds
$deadline = (Get-Date).AddSeconds($BindTimeoutSec)
$bound = $false
while ((Get-Date) -lt $deadline) {
    if (Test-PortListen $Port) { $bound = $true; break }
    Start-Sleep -Milliseconds 200
}
if (-not $bound) {
    try { Stop-Process -Id $sidecarPid -Force -EA Stop } catch {}
    throw "Sidecar did not bind port $Port within $BindTimeoutSec s. Log: $logErr"
}

# Record state
New-Item -ItemType Directory -Force -Path (Split-Path $StateFile) | Out-Null
[ordered]@{
    ws_url          = $WsUrl
    port            = $Port
    sidecar_pid     = $sidecarPid
    show_window     = [bool]$ShowSidecarWindow
    startedAt       = (Get-Date).ToString('o')
    log_out         = $logOut
    log_err         = $logErr
} | ConvertTo-Json | Set-Content -LiteralPath $StateFile -Encoding UTF8

# Launch Desktop with env var
$env:CODEX_APP_SERVER_WS_URL = $WsUrl
$desktop = Start-Process -FilePath $DesktopExe -PassThru

# Wait for Desktop to start spawning child processes, then poll until all gone.
# Electron is multi-process: the launcher's $desktop.Id may exit before the
# renderer/GPU children. We watch the count of Codex.exe by Path.
Start-Sleep -Seconds 2
while ((Get-DesktopProcessCount) -gt 0) { Start-Sleep -Seconds 2 }

# Desktop is fully closed — tear down sidecar.
try { Stop-Process -Id $sidecarPid -Force -EA Stop } catch {}
# Also kill any orphan codex.exe sidecar that may have lingered
Get-Process -Name 'codex' -EA SilentlyContinue |
    Where-Object { $_.Path -eq $SidecarExe } |
    ForEach-Object { try { Stop-Process -Id $_.Id -Force -EA Stop } catch {} }

Remove-Item -LiteralPath $StateFile -Force -EA SilentlyContinue
