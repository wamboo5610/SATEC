@echo off
title SISAT - Subir cambios a GitHub y Vercel
cd /d "%~dp0"
echo ================================================
echo   SUBIR CAMBIOS A GITHUB / VERCEL
echo ================================================
echo.
git status
echo.
set /p MSG="Mensaje del commit (Enter = Actualizacion SISAT): "
if "%MSG%"=="" set MSG=Actualizacion SISAT
git add -A
git commit -m "%MSG%"
if errorlevel 1 (
  echo No hay cambios nuevos o el commit fallo.
  pause
  exit /b 1
)
git push origin main
if errorlevel 1 (
  echo Error al subir a GitHub.
  pause
  exit /b 1
)
echo.
echo Listo. Vercel desplegara en 1-3 minutos.
echo URL: https://sisat.vercel.app
echo.
pause