# Auto-refresh watchdog for Codex Patched.
#
# Use case
# --------
# When the user runs `codex resume -all` or `codex exec --resume <id>` from
# terminal (outside Codex Desktop), separate codex processes write to JSONL
# rollout files. The running Codex Desktop sidecar (`codex.exe` under
# CodexDesktopPatched) does NOT tail JSONL changes from external writers, so
# its cache for those threads goes stale and the UI never reflects progress.
#
# This watchdog polls every N seconds. When it detects a JSONL write newer
# than the sidecar's start time (i.e. the sidecar's cache is definitely
# stale), it kills the sidecar. Electron's `pu` supervisor auto-respawns it,
# the renderer reconnects, Patch D fires to clear the conversations Map, and
# the next thread/read returns fresh disk state.
#
# Throttled: at most one kill per --cooldown seconds (default 60). So worst-
# case visible lag for external writes ≈ poll-interval + cooldown.
#
# Usage
# -----
#   powershell -ExecutionPolicy Bypass -File .\auto-refresh-watchdog.ps1
#   powershell -ExecutionPolicy Bypass -File .\auto-refresh-watchdog.ps1 -Interval 15 -Cooldown 60 -Verbose
#
# To stop: close the window or `Get-Process powershell | Stop-Process` (be
# careful), or use Start-Codex-Auto-Refresh.cmd / Stop-Codex-Auto-Refresh.cmd
# wrappers which manage a PID file.

[CmdletBinding()]
param(
    [int]$Interval = 15,            # seconds between polls
    [int]$Cooldown = 60,            # min seconds between sidecar kills
    [int]$StaleThresholdSec = 30,   # only act on JSONL writes within this recency
    [string]$SessionsDir = "$env:USERPROFILE\.codex\sessions",
    [switch]$Once                   # for testing — one cycle then exit
)

$ErrorActionPreference = 'Continue'
$patchedRoot = Join-Path $env:LOCALAPPDATA 'OpenAI\CodexDesktopPatched'
$logFile = Join-Path $patchedRoot 'logs\auto-refresh-watchdog.log'
New-Item -ItemType Directory -Force -Path (Split-Path $logFile) | Out-Null

function Log($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Add-Content -LiteralPath $logFile -Value $line
    Write-Host $line
}

function Get-PatchedSidecar {
    Get-Process codex -EA SilentlyContinue | Where-Object {
        $_.Path -like "$patchedRoot*\resources\codex.exe"
    } | Select-Object -First 1
}

$lastKillAt = [DateTime]::MinValue

Log "Watchdog started. Interval=${Interval}s Cooldown=${Cooldown}s StaleThreshold=${StaleThresholdSec}s"
Log "Sessions dir: $SessionsDir"

do {
    try {
        $sidecar = Get-PatchedSidecar
        if (-not $sidecar) {
            Log "Codex Patched sidecar not running. Idle."
        } else {
            $now = Get-Date
            $sidecarStart = $sidecar.StartTime
            $sidecarAge = ($now - $sidecarStart).TotalSeconds

            # Find any JSONL written *after* sidecar start AND within stale window.
            $staleAfter = $sidecarStart.AddSeconds(2)   # small grace period
            $recentEnough = $now.AddSeconds(-$StaleThresholdSec)
            $cutoff = if ($staleAfter -gt $recentEnough) { $staleAfter } else { $recentEnough }

            $hits = Get-ChildItem -Recurse $SessionsDir -Filter '*.jsonl' -EA SilentlyContinue |
                Where-Object { $_.LastWriteTime -gt $cutoff }

            if ($hits) {
                $sample = $hits[0]
                $sinceKill = ($now - $lastKillAt).TotalSeconds
                if ($sinceKill -ge $Cooldown -and $sidecarAge -ge 10) {
                    Log ("STALE detected ({0} files). Latest: {1} @ {2}. Killing sidecar PID {3} (age={4}s)" -f `
                        $hits.Count, $sample.Name, $sample.LastWriteTime.ToString('HH:mm:ss'), $sidecar.Id, [int]$sidecarAge)
                    try {
                        Stop-Process -Id $sidecar.Id -Force -EA Stop
                        $lastKillAt = $now
                        Log "Kill OK. Electron pu supervisor will respawn."
                    } catch {
                        Log "Kill FAILED: $_"
                    }
                } else {
                    Log ("Stale detected but cooldown active. sinceKill={0}s sidecarAge={1}s (need >={2}s)" -f `
                        [int]$sinceKill, [int]$sidecarAge, $Cooldown)
                }
            } else {
                # No-op idle log every ~5 polls to keep log noise low
                if ((Get-Random -Maximum 5) -eq 0) {
                    Log ("Idle. Sidecar PID {0} age={1}s, no recent external writes." -f $sidecar.Id, [int]$sidecarAge)
                }
            }
        }
    } catch {
        Log "Watchdog loop error: $_"
    }

    if ($Once) { break }
    Start-Sleep -Seconds $Interval
} while ($true)
