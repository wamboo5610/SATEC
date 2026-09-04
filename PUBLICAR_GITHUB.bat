@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Publicar SISAT en GitHub — WAMBOO TIC

set GIT=
if exist "C:\Program Files\Git\cmd\git.exe" set "GIT=C:\Program Files\Git\cmd\git.exe"
if exist "C:\Program Files (x86)\Git\cmd\git.exe" set "GIT=C:\Program Files (x86)\Git\cmd\git.exe"
where git >nul 2>nul
if not errorlevel 1 set GIT=git
if "%GIT%"=="" (
    echo No se encontro Git. Instale Git para Windows:
    echo https://git-scm.com/download/win
    pause
    exit /b 1
)

echo Repositorio: https://github.com/wamboo5610/SISAT
echo.
echo 1. Cree el repo SISAT en GitHub si aun no existe (publico o privado).
echo 2. Este script sube el codigo. La base de datos local NO se sube.
echo.

if not exist ".git" (
    "%GIT%" init
    "%GIT%" remote remove origin >nul 2>nul
    "%GIT%" remote add origin https://github.com/wamboo5610/SISAT.git
    "%GIT%" branch -M main
)

set /p MSG="Mensaje del commit (Enter = Actualizacion SISAT): "
if "%MSG%"=="" set MSG=Actualizacion SISAT

"%GIT%" add -A
"%GIT%" commit -m "%MSG%"
if errorlevel 1 (
    echo No hay cambios nuevos o el commit fallo.
)
"%GIT%" push -u origin main
if errorlevel 1 (
    echo Error al subir. Inicie sesion en GitHub o cree el repositorio SISAT.
    pause
    exit /b 1
)
echo.
echo Listo. En SISAT: Sistema - Buscar actualizacion.
echo Si el repo es privado, pega un token (permiso repo) en esa pantalla.
pause
