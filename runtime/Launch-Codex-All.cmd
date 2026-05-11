@echo off
REM One-click launch: starts auto-refresh watchdog (if not running) then opens Codex Patched.

setlocal

set "PIDFILE=%LOCALAPPDATA%\OpenAI\CodexDesktopPatched\logs\auto-refresh-watchdog.pid"
set "WATCHDOG=%~dp0auto-refresh-watchdog.ps1"
set "PATCHED_LAUNCHER=%LOCALAPPDATA%\OpenAI\CodexDesktopPatched\Launch-Codex-Patched.vbs"

REM ---- Start watchdog only if not already running ----
set "WATCHDOG_RUNNING="
if exist "%PIDFILE%" (
    set /p RUNNING_PID=<"%PIDFILE%"
    tasklist /FI "PID eq %RUNNING_PID%" 2>nul | findstr /I "powershell" >nul
    if not errorlevel 1 set "WATCHDOG_RUNNING=1"
)

if defined WATCHDOG_RUNNING (
    echo Watchdog already running (PID %RUNNING_PID%). Skipping start.
) else (
    if not exist "%LOCALAPPDATA%\OpenAI\CodexDesktopPatched\logs" mkdir "%LOCALAPPDATA%\OpenAI\CodexDesktopPatched\logs"
    for /f "tokens=*" %%i in ('powershell -NoProfile -Command ^
        "$p = Start-Process -FilePath 'powershell.exe' -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File','%WATCHDOG%' -WindowStyle Hidden -PassThru; $p.Id"') do (
        echo %%i > "%PIDFILE%"
        echo Watchdog started (PID %%i)
    )
)

REM ---- Launch Codex Patched ----
if exist "%PATCHED_LAUNCHER%" (
    start "" wscript.exe "%PATCHED_LAUNCHER%"
    echo Launching Codex Patched...
) else (
    echo ERROR: Codex Patched launcher not found at:
    echo   %PATCHED_LAUNCHER%
    echo Run patch-codex-desktop-recent-window.ps1 first to set things up.
    pause
    exit /b 1
)

REM Auto-close window after 2s — no need to wait
timeout /t 2 /nobreak >nul
endlocal
