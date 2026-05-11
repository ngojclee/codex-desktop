@echo off
REM Soft-refresh Codex Patched by killing codex-command-runner.exe.
REM Electron supervisor auto-respawns the sidecar; the UI window stays open.
REM Use when a thread is stuck partway through updates from cross-session CLI dispatch.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0refresh-codex-app-server.ps1"
echo.
echo Press any key to close...
pause >nul
