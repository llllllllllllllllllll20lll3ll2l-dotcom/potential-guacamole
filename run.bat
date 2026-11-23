@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === НейроКоманда: установка ===
where python >nul 2>nul || (echo Нужен Python 3.10+ : https://www.python.org/downloads/ & pause & exit /b 1)
if not exist .venv (
  echo Создаю окружение .venv ...
  python -m venv .venv
)
call .venv\Scripts\activate.bat
echo Устанавливаю зависимости ...
python -m pip install --upgrade pip >nul
pip install -r requirements.txt
echo Устанавливаю браузер для агентов (Playwright Chromium) ...
playwright install chromium
echo.
echo === Запуск ===
echo Интерфейс откроется в браузере: http://127.0.0.1:8000
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
