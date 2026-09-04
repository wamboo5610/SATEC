@echo off
chcp 65001 >nul
cd /d "%~dp0"
title SATEC servidor LAN — WAMBOO TIC
if not exist "venv\Scripts\python.exe" (
    echo Primero ejecute INSTALAR.bat
    pause
    exit /b 1
)
echo =====================================================
echo  SATEC  modo servidor (red local / ADMS)
echo  Autor: WAMBOO TIC
echo =====================================================
echo.
echo Panel:  http://127.0.0.1:8000
echo ADMS:   http://ESTA-PC:8000/iclock/
echo.
"venv\Scripts\python.exe" "%~dp0run.py"
pause
