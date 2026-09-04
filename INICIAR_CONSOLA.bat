@echo off
chcp 65001 >nul
cd /d "%~dp0"
title SISAT — consola  |  WAMBOO TIC
if not exist "venv\Scripts\python.exe" (
    echo Primero ejecute INSTALAR.bat
    pause
    exit /b 1
)
"venv\Scripts\python.exe" "%~dp0main.py"
if errorlevel 1 pause
