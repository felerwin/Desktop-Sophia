@echo off
setlocal
title Ember
cd /d "%~dp0"
if exist ".portable" (
 set "HF_HOME=%CD%\.cache\huggingface"
 set "XDG_CACHE_HOME=%CD%\.cache"
)
if not exist ".venv\Scripts\python.exe" (
 echo Ember is not installed yet.
 pause
 exit /b 1
)
".venv\Scripts\python.exe" sophia.py
if errorlevel 1 pause
