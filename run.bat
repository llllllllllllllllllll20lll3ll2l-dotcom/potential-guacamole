@echo off
cd /d "%~dp0"
echo === НейроКоманда: установка ===
where python >nul 2>nul || goto nopython
if not exist .venv\ (
  echo Создаю окружение .venv ...
  python -m venv .venv
)
call .venv\Scripts\activate.bat
echo Устанавливаю зависимости ...
python -m pip install --upgrade pip >nul
pip install -r requirements.txt
echo Ставлю браузер агентов: Playwright Chromium ...
playwright install chromium
echo.
echo === Запуск: интерфейс откроется на http://127.0.0.1:8000 ===
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
exit /b 0

:nopython
echo НЕ НАШЁЛ Python в PATH!
echo Скачай Python 3.10 или новее: https://www.python.org/downloads/
echo При установке ОБЯЗАТЕЛЬНО отметь галочку Add python.exe to PATH
echo Затем открой НОВУЮ консоль и запусти run.bat снова.
pause
exit /b 1
