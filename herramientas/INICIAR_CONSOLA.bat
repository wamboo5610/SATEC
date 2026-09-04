@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
title SATEC — consola  |  WAMBOO TIC
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" "%cd%\main.py"
) else if exist "runtime\python.exe" (
    "runtime\python.exe" "%cd%\main.py"
) else (
    echo Primero ejecute INSTALAR.bat
)
if errorlevel 1 pause
