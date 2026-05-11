@echo off
REM Start the Codex auto-refresh watchdog in a hidden background window.
REM Stop with Stop-Codex-Auto-Refresh.cmd.

set "PIDFILE=%LOCALAPPDATA%\OpenAI\CodexDesktopPatched\logs\auto-refresh-watchdog.pid"

REM Refuse to start if already running.
if exist "%PIDFILE%" (
    set /p RUNNING_PID=<"%PIDFILE%"
    tasklist /FI "PID eq %RUNNING_PID%" 2>nul | findstr /I "powershell" >nul
    if not errorlevel 1 (
        echo Watchdog already running with PID %RUNNING_PID%. Use Stop-Codex-Auto-Refresh.cmd first.
        pause
        exit /b 1
    )
    del "%PIDFILE%"
)

set "SCRIPT=%~dp0auto-refresh-watchdog.ps1"

REM Launch hidden, capture PID to PIDFILE.
for /f "tokens=*" %%i in ('powershell -NoProfile -Command ^
    "$p = Start-Process -FilePath 'powershell.exe' -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File','%SCRIPT%' -WindowStyle Hidden -PassThru; $p.Id"') do (
    echo %%i > "%PIDFILE%"
    echo Started watchdog PID %%i. Log: %LOCALAPPDATA%\OpenAI\CodexDesktopPatched\logs\auto-refresh-watchdog.log
)

echo.
echo Press any key to close...
pause >nul
