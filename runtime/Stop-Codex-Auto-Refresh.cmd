@echo off
REM Stop the Codex auto-refresh watchdog if running.

set "PIDFILE=%LOCALAPPDATA%\OpenAI\CodexDesktopPatched\logs\auto-refresh-watchdog.pid"

if not exist "%PIDFILE%" (
    echo No PID file found. Watchdog likely not running.
    REM Best-effort: kill any powershell process that has auto-refresh-watchdog in its command line
    powershell -NoProfile -Command ^
        "$ps = Get-CimInstance Win32_Process -Filter \"Name='powershell.exe'\" | Where-Object { $_.CommandLine -like '*auto-refresh-watchdog.ps1*' }; if ($ps) { $ps | ForEach-Object { Write-Host ('Killing stray watchdog PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force } } else { Write-Host 'No stray watchdog process found.' }"
    pause
    exit /b 0
)

set /p PID=<"%PIDFILE%"
echo Stopping watchdog PID %PID%...
taskkill /PID %PID% /F 2>nul
if errorlevel 1 (
    echo Process %PID% was not running. Cleaning stale PID file.
) else (
    echo Stopped.
)
del "%PIDFILE%"

echo.
echo Press any key to close...
pause >nul
