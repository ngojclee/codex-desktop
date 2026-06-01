@echo off
REM Launch Codex Desktop with shared sidecar lifecycle in BUILD_FLAVOR=dev.

setlocal
set "PS1=%~dp0Launch-Codex.ps1"
if not exist "%PS1%" (
    echo Cannot find Launch-Codex.ps1 next to this .cmd
    pause
    exit /b 1
)
start "" /B powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%PS1%" -BuildFlavor dev
endlocal
exit /b 0
