@echo off
REM One-click update Codex (GitHub Patched).
REM Copies the updater .ps1 to %TEMP% first so PowerShell doesn't hold a file
REM handle inside the install dir, allowing the rename/swap step to succeed.

setlocal
set "TEMPSCRIPT=%TEMP%\Update-Codex-%RANDOM%%RANDOM%.ps1"
copy /Y "%~dp0Update-Codex.ps1" "%TEMPSCRIPT%" >nul
if errorlevel 1 (
    echo Failed to stage updater script to %TEMPSCRIPT%
    pause
    exit /b 1
)

REM Move CMD's CWD out of CodexFromGithub before launching PS — otherwise
REM CMD's CWD holds a handle on the install dir and Rename-Item fails.
cd /d "%TEMP%"

powershell -NoProfile -ExecutionPolicy Bypass -File "%TEMPSCRIPT%"
set RC=%ERRORLEVEL%

del /Q "%TEMPSCRIPT%" >nul 2>nul

echo.
if %RC% NEQ 0 (
    echo Updater exited with code %RC%.
)
echo Press any key to close...
pause >nul
exit /b %RC%
