@echo off
setlocal
title Desktop Ember Installer
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_windows.ps1"
if errorlevel 1 (
    echo.
    echo Installation did not finish. Review the message above and try again.
    pause
    exit /b 1
)

echo.
echo Desktop Ember is installed.
echo Add your OpenAI API key to .env, then double-click run_ember.bat.
if exist ".env" start "" notepad.exe "%~dp0.env"
pause
