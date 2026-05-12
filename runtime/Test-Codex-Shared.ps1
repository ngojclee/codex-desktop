# Test-Codex-Shared.ps1
#
# Starts a shared Codex app-server on ws://127.0.0.1:24567 and launches Codex
# Desktop with CODEX_APP_SERVER_WS_URL pointed at it. Desktop will skip spawning
# its own private sidecar and use the WS transport instead. CLI commands can
# then connect to the SAME sidecar with `--remote ws://127.0.0.1:24567`, so
# events flow into Desktop's renderer in real time.
#
# Usage:
#   .\Test-Codex-Shared.ps1                  # kill+start sidecar+launch Desktop
#   .\Test-Codex-Shared.ps1 -NoLaunchDesktop # just start the sidecar
#   .\Test-Codex-Shared.ps1 -NoKill          # don't pre-kill existing Codex

[CmdletBinding()]
param(
    [int]$Port = 24567,
    [switch]$NoKill,
    [switch]$NoLaunchDesktop
)

$ErrorActionPreference = 'Stop'

$InstallDir = "$env:LOCALAPPDATA\CodexFromGithub"
$Sidecar    = Join-Path $InstallDir 'resources\codex.exe'
$Desktop    = Join-Path $InstallDir 'Codex.exe'
$WsUrl      = "ws://127.0.0.1:$Port"

if (-not (Test-Path -LiteralPath $Sidecar)) { throw "Sidecar exe missing: $Sidecar" }
if (-not (Test-Path -LiteralPath $Desktop)) { throw "Desktop exe missing: $Desktop" }

function Test-PortInUse([int]$p) {
    return (netstat -ano | Select-String ":$p " -SimpleMatch) -ne $null
}

if (-not $NoKill) {
    Write-Host "[1/5] Stopping any running Codex processes..."
    Get-Process Codex,codex -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Host "      kill PID $($_.Id) ($($_.Name))"
        try { Stop-Process -Id $_.Id -Force -ErrorAction Stop } catch { Write-Host "        (already gone)" }
    }
    Start-Sleep -Milliseconds 800
}

Write-Host "[2/5] Checking port $Port is free..."
if (Test-PortInUse $Port) {
    throw "Port $Port already in use. Run Test-Codex-Shared.ps1 -NoKill is not appropriate here; clean up the other listener first."
}

$logDir = Join-Path $env:TEMP "codex-shared"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$ts = Get-Date -Format yyyyMMdd-HHmmss
$sidecarLog = Join-Path $logDir "app-server-$ts.log"

# Launch sidecar in its OWN visible PowerShell window so live logs are visible
# without cluttering the launcher terminal. Tee-Object also archives to disk.
Write-Host "[3/5] Starting shared sidecar (separate window) -> $WsUrl"
Write-Host "      exe: $Sidecar"
Write-Host "      log: $sidecarLog"
$inner = "`$Host.UI.RawUI.WindowTitle='Codex Shared Sidecar  ($WsUrl)'; " +
         "Write-Host 'Codex shared sidecar — close this window to stop it.' -Fore Yellow; " +
         "& '$Sidecar' app-server --listen '$WsUrl' 2>&1 | Tee-Object -FilePath '$sidecarLog'"
$proc = Start-Process powershell.exe `
    -ArgumentList @('-NoExit','-NoProfile','-Command',$inner) `
    -PassThru
Write-Host "      window PID: $($proc.Id)"

# Wait for the sidecar to bind (max 8s). We can't rely on $proc.HasExited
# because $proc wraps the host PowerShell window (-NoExit), not codex.exe.
$deadline = (Get-Date).AddSeconds(8)
$listening = $false
while ((Get-Date) -lt $deadline) {
    if (Test-PortInUse $Port) { $listening = $true; break }
    Start-Sleep -Milliseconds 200
}
if (-not $listening) {
    throw "Sidecar did not bind port $Port within 8s. Check log: $sidecarLog"
}
Write-Host "[4/5] Sidecar listening on $WsUrl"

# Record port + PID so the watchdog/CLI shim can find them later.
$controlDir = Join-Path $env:USERPROFILE '.codex'
New-Item -ItemType Directory -Force -Path $controlDir | Out-Null
$state = [ordered]@{
    ws_url       = $WsUrl
    port         = $Port
    host_pid     = $proc.Id  # PowerShell host window; sidecar is its child
    startedAt    = (Get-Date).ToString('o')
    log          = $sidecarLog
}
$state | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $controlDir 'desktop-shared-app-server.json') -Encoding UTF8

if (-not $NoLaunchDesktop) {
    Write-Host "[5/5] Launching Codex Desktop with CODEX_APP_SERVER_WS_URL=$WsUrl"
    $env:CODEX_APP_SERVER_WS_URL = $WsUrl
    # Start-Process inherits parent env (current PS session has the var set)
    Start-Process -FilePath $Desktop
}
else {
    Write-Host "[5/5] Skipped Desktop launch (NoLaunchDesktop)."
}

Write-Host ""
Write-Host "Done. State file: $($controlDir)\desktop-shared-app-server.json"
Write-Host "CLI can connect with:"
Write-Host "  & `"$Sidecar`" resume --remote `"$WsUrl`" --last"
Write-Host ""
Write-Host "Stop everything later with:"
Write-Host "  Get-Process Codex,codex -EA SilentlyContinue | Stop-Process -Force"
