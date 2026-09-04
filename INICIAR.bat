@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "venv\Scripts\pythonw.exe" (
    start "" "venv\Scripts\pythonw.exe" "%~dp0main.py"
    exit /b 0
)
if exist "runtime\pythonw.exe" (
    start "" "runtime\pythonw.exe" "%~dp0main.py"
    exit /b 0
)
echo Primero ejecute INSTALAR.bat
pause
exit /b 1
