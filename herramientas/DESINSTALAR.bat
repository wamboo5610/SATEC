@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
title Desinstalar SATEC — WAMBOO TIC
echo Esto quita los accesos directos.
echo La carpeta del programa y la base de datos NO se borran.
echo.
del "%USERPROFILE%\Desktop\SATEC WAMBOO TIC.lnk" >nul 2>&1
del "%USERPROFILE%\Escritorio\SATEC WAMBOO TIC.lnk" >nul 2>&1
del "%USERPROFILE%\Desktop\SISAT WAMBOO TIC.lnk" >nul 2>&1
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\WAMBOO TIC\SATEC.lnk" >nul 2>&1
echo Accesos directos eliminados.
pause
