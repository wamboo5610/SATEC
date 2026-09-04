@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Crear instalador SATEC — WAMBOO TIC
if not exist "venv\Scripts\python.exe" (
    echo Primero ejecute INSTALAR.bat
    pause
    exit /b 1
)
echo Empaquetando instalador...
"venv\Scripts\python.exe" installer\pack.py
if errorlevel 1 (
    echo No se pudo crear el instalador.
    pause
    exit /b 1
)
echo.
echo Listo. El ZIP está en la carpeta dist\
echo Copie ese ZIP a la PC destino, descomprima y ejecute SETUP.bat
pause
