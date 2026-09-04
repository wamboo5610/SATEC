@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
title Crear instalador SATEC — WAMBOO TIC
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" installer\pack.py
) else if exist "runtime\python.exe" (
    "runtime\python.exe" installer\pack.py
) else (
    echo Primero ejecute INSTALAR.bat
    pause
    exit /b 1
)
if errorlevel 1 (
    echo No se pudo crear el instalador.
    pause
    exit /b 1
)
echo.
echo Listo. El ZIP esta en dist\
echo Copielo a cada PC, descomprima y ejecute INSTALAR.bat
pause
