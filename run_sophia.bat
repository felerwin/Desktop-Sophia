@echo off
setlocal
title Desktop Sophia
cd /d "%~dp0"

rem Keep caches beside Sophia when running from a flash drive.
set "SOPHIA_ROOT=%~dp0"
set "HF_HOME=%~dp0.cache\huggingface"
set "XDG_CACHE_HOME=%~dp0.cache"
set "PYTHONPYCACHEPREFIX=%~dp0.cache\pycache"
if not exist ".cache" mkdir ".cache" >nul 2>&1

if not exist ".venv\Scripts\python.exe" (
    echo Desktop Sophia has not been installed on this drive yet.
    echo Double-click setup_windows.bat first.
    pause
    exit /b 1
)

if not exist ".env" copy /y ".env.example" ".env" >nul
if not exist "config.json" copy /y "config.example.json" "config.json" >nul

".venv\Scripts\python.exe" sophia.py

if errorlevel 1 pause
