@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
title SATEC servidor LAN — WAMBOO TIC
echo  SATEC  modo servidor (red local / ADMS)
echo  Panel:  http://127.0.0.1:8000
echo.
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" "%cd%\run.py"
) else if exist "runtime\python.exe" (
    "runtime\python.exe" "%cd%\run.py"
) else (
    echo Primero ejecute INSTALAR.bat
)
pause
