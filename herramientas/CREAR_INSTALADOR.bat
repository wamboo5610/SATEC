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
echo Listo. En la carpeta dist\ quedo:
echo  - el instalador EXE (doble clic en cada PC)
echo  - el ZIP de respaldo
pause
