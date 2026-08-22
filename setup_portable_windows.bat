@echo off
setlocal
cd /d "%~dp0"
title Desktop Sophia Portable Setup
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_windows.ps1" -Portable
if errorlevel 1 (
    echo.
    echo Portable setup failed. See the error above.
    pause
    exit /b 1
)
echo.
echo Portable Sophia is ready on this drive.
pause
