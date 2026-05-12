@echo off
REM Launch Codex Desktop with shared sidecar lifecycle.
REM Replaces the default Codex.exe shortcut: starts a sidecar, sets
REM CODEX_APP_SERVER_WS_URL, launches Codex.exe, then kills the sidecar when
REM the Electron app exits.

setlocal
set "PS1=%~dp0Launch-Codex.ps1"
if not exist "%PS1%" (
    echo Cannot find Launch-Codex.ps1 next to this .cmd
    pause
    exit /b 1
)
start "" /B powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%PS1%"
endlocal
exit /b 0
