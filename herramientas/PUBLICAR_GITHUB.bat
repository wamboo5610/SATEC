@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
title Publicar SATEC en GitHub — WAMBOO TIC

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

set REPO=https://github.com/wamboo5610/SATEC.git
echo.
echo =====================================================
echo  Publicar SATEC  (NO se usa el repo SISAT)
echo  Destino: %REPO%
echo =====================================================
echo.
echo SISAT es el sistema WEB. Este escritorio se publica en SATEC.
echo IMPORTANTE: la base de datos (data\attendance.db) NO se sube.
echo Tampoco se suben cuentas, descargas locales ni reportes Excel.
echo.

if not exist ".git" (
    "%GIT%" init
    "%GIT%" branch -M main
)

"%GIT%" remote remove origin >nul 2>nul
"%GIT%" remote add origin "%REPO%"
"%GIT%" branch -M main
echo Remoto origin = %REPO%
echo.

set /p MSG="Mensaje del commit (Enter = Actualizacion SATEC): "
if "%MSG%"=="" set MSG=Actualizacion SATEC

"%GIT%" add -A
"%GIT%" reset -q -- "data/attendance.db" "data/auth.json" "data/" "data/.gitkeep" 2>nul
"%GIT%" add -f -- "data/.gitkeep" 2>nul
"%GIT%" diff --cached --name-only | findstr /I /C:"attendance.db" /C:"auth.json" /C:".db" /C:".sqlite" >nul
if not errorlevel 1 (
    echo.
    echo ERROR: se iba a subir la base de datos. Se cancelo el envio.
    echo La carpeta data\ no se publica en GitHub.
    "%GIT%" reset -q
    pause
    exit /b 1
)

"%GIT%" commit -m "%MSG%"
if errorlevel 1 (
    echo No hay archivos nuevos. Se subira el ultimo commit.
)

echo.
echo Subiendo a GitHub SATEC...
"%GIT%" push -u origin main
if not errorlevel 1 goto :ok

echo.
echo No se pudo subir. Suele faltar el repositorio SATEC en GitHub.
echo Se abrira el navegador. Cree un repo VACIO llamado SATEC
echo (sin README, sin .gitignore y sin licencia) y vuelva aqui.
echo.
start "" "https://github.com/new?name=SATEC"
echo Cuando el repo SATEC exista, presione una tecla para reintentar.
pause >nul
"%GIT%" push -u origin main
if errorlevel 1 (
    echo.
    echo Sigue fallando. Cree el repo https://github.com/wamboo5610/SATEC
    echo vacio y ejecute otra vez PUBLICAR_GITHUB.bat
    pause
    exit /b 1
)

:ok
echo.
echo Listo. Se subio el codigo de SATEC.
echo La base de datos se quedo en esta PC.
pause
