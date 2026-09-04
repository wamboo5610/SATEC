@echo off
title SISAT - Sincronizar datos locales hacia Vercel
cd /d "%~dp0"
echo ================================================
echo   SINCRONIZAR BASE DE DATOS CON VERCEL
echo ================================================
echo.
echo 1) Este script exporta un respaldo de tu PC local.
echo 2) Luego entra a https://sisat.vercel.app
echo 3) Relojes Biometricos - Restaurar respaldo
echo 4) Sube el ZIP que se abrira en Descargas.
echo.
set BACKUP_DIR=%~dp0RESPALDOS_VERCEL
if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmm"') do set STAMP=%%i
set OUT=%BACKUP_DIR%\sisat_para_vercel_%STAMP%.zip
echo Exportando respaldo...
powershell -NoProfile -Command ^
  "$ProgressPreference='SilentlyContinue';" ^
  "$s = New-Object Microsoft.PowerShell.Commands.WebRequestSession;" ^
  "$login = @{username='admin';password='Admin2026'} | ConvertTo-Json;" ^
  "try { Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/auth/login' -Method POST -Body $login -ContentType 'application/json' -WebSession $s | Out-Null } catch { Write-Host 'Inicia INICIAR.bat y usa tu clave local.'; exit 1 };" ^
  "Invoke-WebRequest -Uri 'http://127.0.0.1:8000/api/backup/export' -WebSession $s -OutFile '%OUT%';" ^
  "Write-Host 'Respaldo guardado en %OUT%'"
if errorlevel 1 (
  echo.
  echo No se pudo exportar. Asegurate de tener INICIAR.bat corriendo.
  pause
  exit /b 1
)
echo.
echo Respaldo listo: %OUT%
start "" "%BACKUP_DIR%"
echo Abre Vercel y restaura ese archivo ZIP.
pause