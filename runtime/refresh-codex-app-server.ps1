# Soft-refresh Codex Patched by killing the app-server sidecar.
#
# Why this exists
# ---------------
# The app-server sidecar (lowercase `codex.exe` under
#   %LOCALAPPDATA%\OpenAI\CodexDesktopPatched\<pkg>\app\resources\codex.exe
# Older builds shipped it as `codex-command-runner.exe` — we kill either.
#
# It holds an in-memory cache of each thread's state and does NOT tail JSONL
# writes from external CLI processes. When session A delegates to B via
# `codex exec --resume <B>` from outside this app-server, the sidecar's cache
# for B goes stale and the renderer's `thread/turns/list` returns stale data.
# UI top-of-list reorders but content stops updating.
#
# Electron main has a process supervisor (class `pu` in workspace-root-drop-
# handler-*.js) that handles `onProcessExit` -> `scheduleRestart` -> spawn
# new sidecar. The renderer's `AppServerConnection` class (`Of`) has its
# own `reconnectTimer` that re-attaches when the sidecar comes back.
#
# So killing the sidecar process triggers a clean restart. The UI window
# stays open. After ~1-3 seconds the renderer reconnects, re-reads thread
# state from disk, and the stuck thread shows its full content.
#
# Note: this only helps if cache lives in the sidecar. If the renderer
# itself holds stale state across reconnect (it has a `markAllConversations
# NeedResumeAfterReconnect` path that preserves the conversations map),
# this kill alone may not be enough. In that case the renderer needs a
# reload too — see `--reload` flag below.

[CmdletBinding()]
param(
    [switch]$Reload   # Also send Ctrl+Shift+R to focused Codex window after kill
)

$ErrorActionPreference = 'Stop'

$patchedRoot = Join-Path $env:LOCALAPPDATA 'CodexFromGithub'

# Find sidecar candidates: lowercase `codex` or `codex-command-runner` whose
# Path is under the patched copy. Exclude PID matches that are actually the
# Electron `Codex.exe` (uppercase resolves the same on Windows but the Path
# resolves under \app\Codex.exe not \app\resources\codex.exe).
$candidates = Get-Process -ErrorAction SilentlyContinue codex,'codex-command-runner' |
    Where-Object {
        $_.Path -and
        $_.Path -like "$patchedRoot*" -and
        $_.Path -like '*\resources\*'
    }

if (-not $candidates) {
    Write-Host "No patched sidecar (codex.exe / codex-command-runner.exe) running."
    Write-Host "(Looking under: $patchedRoot\resources\)"
    Write-Host "Is Codex Patched open?"
    exit 1
}

foreach ($p in $candidates) {
    Write-Host ("Killing PID {0,-6} {1}  ({2:N0} MB)" -f $p.Id, $p.Name, ($p.WorkingSet64/1MB))
    Write-Host ("  Path: {0}" -f $p.Path)
    Stop-Process -Id $p.Id -Force
}

Write-Host ""
Write-Host "Sidecar killed. Electron supervisor will respawn in ~1-3s."

if ($Reload) {
    Start-Sleep -Milliseconds 1500
    Write-Host "Forcing renderer reload via Ctrl+Shift+R on Codex window..."
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $wshell = New-Object -ComObject WScript.Shell
        if ($wshell.AppActivate('Codex')) {
            Start-Sleep -Milliseconds 300
            $wshell.SendKeys('^+r')
            Write-Host "Sent Ctrl+Shift+R."
        } else {
            Write-Host "Could not focus Codex window; activate it and press Ctrl+Shift+R manually."
        }
    } catch {
        Write-Host "Reload key send failed: $_"
    }
}
