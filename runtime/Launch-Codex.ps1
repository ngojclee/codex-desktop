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
# If Codex Desktop is already running on the shared WS sidecar, this script
# defers to Electron's single-instance handling and brings the window forward.
# If Desktop is running on its private stdio sidecar, the launcher restarts the
# install so CODEX_APP_SERVER_WS_URL is present before Electron boots.
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

function Get-MarketplacePluginNames([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return @() }
    try {
        $json = Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
        $plugins = @($json.plugins)
        return @($plugins | ForEach-Object {
            if ($_ -is [string]) {
                $_
            } elseif ($_.PSObject.Properties.Name -contains 'name') {
                [string]$_.name
            } elseif ($_.PSObject.Properties.Name -contains 'id') {
                [string]$_.id
            }
        } | Where-Object { $_ })
    } catch {
        return @()
    }
}

function Reset-StaleComputerUseMarketplaceCache {
    $bundleMarketplace = Join-Path $InstallDir 'resources\plugins\openai-bundled\.agents\plugins\marketplace.json'
    $runtimeRoot = Join-Path $env:USERPROFILE '.codex\.tmp\bundled-marketplaces\openai-bundled'
    $runtimeMarketplace = Join-Path $runtimeRoot '.agents\plugins\marketplace.json'

    $bundlePlugins = Get-MarketplacePluginNames $bundleMarketplace
    if ($bundlePlugins -notcontains 'computer-use') { return }

    $runtimePlugins = Get-MarketplacePluginNames $runtimeMarketplace
    if ($runtimePlugins -contains 'computer-use') { return }

    # The generated marketplace is a cache. If it was produced before Patch J
    # or before a 26.527+ bundle, remove it so Desktop reconciles plugins again.
    Remove-Item -LiteralPath $runtimeRoot -Recurse -Force -ErrorAction SilentlyContinue
}

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

function Get-InstallProcesses {
    @(Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -and
        $_.ExecutablePath.StartsWith($InstallDir, [System.StringComparison]::OrdinalIgnoreCase)
    })
}

function Get-InstallSidecars {
    @(Get-InstallProcesses | Where-Object {
        $_.Name -ieq 'codex.exe' -and $_.CommandLine -match '\bapp-server\b'
    })
}

function Test-HealthyStateFile {
    if (-not (Test-Path -LiteralPath $StateFile)) { return $false }
    try {
        $state = Get-Content -Raw -LiteralPath $StateFile | ConvertFrom-Json
        if (-not $state.port) { return $false }
        $r = Invoke-WebRequest "http://127.0.0.1:$($state.port)/healthz" -UseBasicParsing -TimeoutSec 2
        return $r.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Stop-InstallProcesses {
    $procs = Get-InstallProcesses
    foreach ($p in $procs) {
        try { Stop-Process -Id $p.ProcessId -Force -EA Stop } catch {}
    }

    $deadline = (Get-Date).AddSeconds(8)
    while ((Get-Date) -lt $deadline) {
        if ((Get-InstallProcesses).Count -eq 0) { return }
        Start-Sleep -Milliseconds 250
    }
}

function Start-LogTailWindow {
    param(
        [Parameter(Mandatory=$true)]
        [string[]]$Paths,
        [string]$Title = 'Codex Shared Sidecar Logs'
    )

    $existing = @($Paths | Where-Object { $_ -and (Test-Path -LiteralPath $_) })
    if ($existing.Count -eq 0) {
        Write-Host "No sidecar log file found yet."
        return
    }

    $literalPaths = ($existing | ForEach-Object {
        "'" + ($_ -replace "'", "''") + "'"
    }) -join ','
    $safeTitle = $Title -replace "'", "''"
    $script = @"
`$Host.UI.RawUI.WindowTitle = '$safeTitle'
Write-Host 'Tailing Codex sidecar log(s). Close this window when done.' -Fore Yellow
Write-Host ''
`$paths = @($literalPaths)
foreach (`$path in `$paths) {
    if (Test-Path -LiteralPath `$path) {
        Write-Host "----- `$path -----" -Fore Cyan
        Get-Content -LiteralPath `$path -Tail 40
        Write-Host ''
    }
}
Write-Host 'Watching for new log lines...' -Fore Yellow
`$jobs = @()
foreach (`$path in `$paths) {
    if (-not (Test-Path -LiteralPath `$path)) { continue }
    `$jobs += Start-Job -ArgumentList `$path -ScriptBlock {
        param([string]`$Path)
        Get-Content -LiteralPath `$Path -Tail 0 -Wait | ForEach-Object {
            '[{0}] {1}' -f [IO.Path]::GetFileName(`$Path), `$_
        }
    }
}
try {
    while (`$true) {
        if (`$jobs.Count -gt 0) { Receive-Job -Job `$jobs }
        Start-Sleep -Milliseconds 500
    }
} finally {
    if (`$jobs.Count -gt 0) {
        `$jobs | Stop-Job -ErrorAction SilentlyContinue
        `$jobs | Remove-Job -Force -ErrorAction SilentlyContinue
    }
}
"@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
    Start-Process powershell.exe -ArgumentList @('-NoExit','-NoProfile','-EncodedCommand',$encoded) | Out-Null
}

function Start-CurrentSidecarLogWindow {
    if (-not (Test-Path -LiteralPath $StateFile)) {
        Write-Host "No shared-sidecar state file found at $StateFile."
        return
    }
    try {
        $state = Get-Content -Raw -LiteralPath $StateFile | ConvertFrom-Json
        Start-LogTailWindow -Paths @($state.log_out, $state.log_err) -Title "Codex Shared Sidecar Logs ($($state.ws_url))"
    } catch {
        Write-Host "Could not open current sidecar logs: $_"
    }
}

# Honor an existing live Desktop instance only when it is already on the shared
# sidecar. If Desktop was opened directly, it will have spawned a private stdio
# app-server and must be restarted with CODEX_APP_SERVER_WS_URL in its env.
if ((Get-DesktopProcessCount) -gt 0) {
    $sidecars = Get-InstallSidecars
    $privateSidecars = @($sidecars | Where-Object { $_.CommandLine -notmatch '--listen\s+ws://127\.0\.0\.1:' })
    $sharedSidecars = @($sidecars | Where-Object { $_.CommandLine -match '--listen\s+ws://127\.0\.0\.1:' })

    if ($privateSidecars.Count -eq 0 -and $sharedSidecars.Count -gt 0 -and (Test-HealthyStateFile)) {
        Write-Host "Codex Desktop already running on shared sidecar - focusing existing window."
        if ($ShowSidecarWindow) {
            Start-CurrentSidecarLogWindow
        }
        Start-Process -FilePath $DesktopExe
        return
    }

    Write-Host "Codex Desktop is running without the shared sidecar - restarting into shared WS mode."
    Stop-InstallProcesses
}

# Cleanup any stale state file pointing at a dead sidecar.
if (Test-Path -LiteralPath $StateFile) {
    try {
        $stale = Get-Content -Raw -LiteralPath $StateFile | ConvertFrom-Json
        if ($stale.sidecar_pid) {
            try { Stop-Process -Id $stale.sidecar_pid -Force -EA Stop } catch {}
        }
        if ($stale.host_pid) {
            try { Stop-Process -Id $stale.host_pid -Force -EA Stop } catch {}
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
    # Visible window - handy for debugging
    $inner = "`$Host.UI.RawUI.WindowTitle = 'Codex Shared Sidecar ($WsUrl)'; " +
             "Write-Host 'Sidecar - close this window to stop Codex.' -Fore Yellow; " +
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

# Launch Desktop with env vars
$env:CODEX_APP_SERVER_WS_URL = $WsUrl

# --- Computer Use unlock (Patch J) ---
# The bundled plugin reconciliation for computer-use on Windows requires:
#   1. isInternal(buildFlavor) - only 'dev','agent','nightly','owl','internal-alpha' pass.
#   2. features.computerUse === true - server-delivered feature flag.
# The Haleclipse rebuild ships codexBuildFlavor=prod which fails (1).
# Setting BUILD_FLAVOR=owl keeps the Owl shell lane while making isInternal pass.
# CODEX_ELECTRON_ENABLE_WINDOWS_COMPUTER_USE=1 forces the feature flag (2).
#
# NOTE: These env vars are necessary but not sufficient on Windows. The plugin
# files (computer-use folder + node_modules/@oai/sky) must also exist in the
# bundle at resources/plugins/openai-bundled/plugins/computer-use/. Without
# those files, the reconciliation has nothing to materialize. On macOS, the
# plugin ships in the app bundle and only needs features.computerUse=true.
if (-not $env:BUILD_FLAVOR) {
    $env:BUILD_FLAVOR = 'owl'
}
if (-not $env:CODEX_ELECTRON_ENABLE_WINDOWS_COMPUTER_USE) {
    $env:CODEX_ELECTRON_ENABLE_WINDOWS_COMPUTER_USE = '1'
}
Reset-StaleComputerUseMarketplaceCache

$desktopArgs = @()
if ($env:CODEX_ELECTRON_PROXY_SERVER) {
    $desktopArgs += "--proxy-server=$env:CODEX_ELECTRON_PROXY_SERVER"
}
if ($env:CODEX_ELECTRON_PROXY_BYPASS_LIST) {
    $desktopArgs += "--proxy-bypass-list=$env:CODEX_ELECTRON_PROXY_BYPASS_LIST"
}
if ($desktopArgs.Count -gt 0) {
    $desktop = Start-Process -FilePath $DesktopExe -ArgumentList $desktopArgs -PassThru
} else {
    $desktop = Start-Process -FilePath $DesktopExe -PassThru
}

# Wait for Desktop to start spawning child processes, then poll until all gone.
# Electron is multi-process: the launcher's $desktop.Id may exit before the
# renderer/GPU children. We watch the count of Codex.exe by Path.
Start-Sleep -Seconds 2
while ((Get-DesktopProcessCount) -gt 0) { Start-Sleep -Seconds 2 }

# Desktop is fully closed - tear down sidecar.
try { Stop-Process -Id $sidecarPid -Force -EA Stop } catch {}
# Also kill any orphan codex.exe sidecar that may have lingered
Get-Process -Name 'codex' -EA SilentlyContinue |
    Where-Object { $_.Path -eq $SidecarExe } |
    ForEach-Object { try { Stop-Process -Id $_.Id -Force -EA Stop } catch {} }

Remove-Item -LiteralPath $StateFile -Force -EA SilentlyContinue
