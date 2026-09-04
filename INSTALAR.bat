@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Instalar SATEC — WAMBOO TIC
echo.
echo  SATEC  Sistema de Asistencia Tecnico
echo  Instalador  |  WAMBOO TIC
echo  La base de datos existente se conserva.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\instalar.ps1" -Mode InPlace
if errorlevel 1 (
    echo.
    echo La instalacion no termino. Si falta Python, el instalador intenta
    echo descargar una copia portatil. Revise internet y vuelva a intentar.
    pause
    exit /b 1
)
echo.
pause
