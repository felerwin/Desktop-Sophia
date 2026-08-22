@echo off
setlocal
cd /d "%~dp0"
title Update Ember
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0update_windows.ps1"
if errorlevel 1 (
    echo.
    echo Ember was not updated. Review the message above; her personal files were not replaced.
    pause
    exit /b 1
)
echo.
echo Ember is current.
pause
