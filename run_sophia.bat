@echo off
setlocal
title Desktop Sophia
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Desktop Sophia has not been installed yet.
    echo Double-click setup_windows.bat first.
    pause
    exit /b 1
)

if not exist ".env" copy /y ".env.example" ".env" >nul
if not exist "config.json" copy /y "config.example.json" "config.json" >nul

".venv\Scripts\python.exe" sophia.py

if errorlevel 1 pause
