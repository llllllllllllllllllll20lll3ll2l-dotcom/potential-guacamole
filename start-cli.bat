@echo off
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
    echo [!] Python не найден в PATH. Установи Python 3.11+ x64 и отметь "Add to PATH".
    pause
    exit /b 1
)
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)
%PY% main.py --cli
pause
