@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Instalar SATEC — WAMBOO TIC

echo =====================================================
echo  SATEC  Sistema de Asistencia Tecnico  v2.3
echo  Aplicacion de escritorio para PC
echo  Autor: WAMBOO TIC
echo =====================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo No se encontro Python. Instale Python 3.12 o superior.
    echo https://www.python.org/downloads/
    pause
    exit /b 1
)

echo Creando entorno virtual...
python -m venv venv
if errorlevel 1 (
    echo No se pudo crear el entorno virtual.
    pause
    exit /b 1
)

echo Instalando dependencias...
"venv\Scripts\python.exe" -m pip install --upgrade pip
"venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Fallo la instalacion de dependencias.
    pause
    exit /b 1
)

echo Verificando motor local...
"venv\Scripts\python.exe" -c "from app.version import AUTHOR, APP_TITLE; from app import database, auth; database.init_db(); auth.init_auth(); print('OK', APP_TITLE, '-', AUTHOR)"
if errorlevel 1 (
    echo El motor de asistencia no arranco.
    pause
    exit /b 1
)

powershell -NoProfile -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop') + '\SATEC WAMBOO TIC.lnk'); ^
   $s.TargetPath='%~dp0INICIAR.bat'; ^
   $s.WorkingDirectory='%~dp0'; ^
   $s.IconLocation='%~dp0assets\icon.ico'; ^
   $s.Description='SATEC - Sistema de Asistencia Tecnico - WAMBOO TIC'; ^
   $s.Save()"

echo.
echo Instalacion lista.
echo Ejecute INICIAR.bat o use el acceso directo del escritorio.
echo Acceso inicial: usuario admin / contraseña admin123
echo Cambie la contraseña al ingresar.
pause
