@echo off
setlocal

taskkill /F /IM Codex.exe >nul 2>nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0patch-codex-desktop-recent-window.ps1" -Limit 1000 -Launch
exit /b %ERRORLEVEL%
