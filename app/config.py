"""Настройки НейроКоманды: пути, модели, ограничения безопасности."""
import os
from pathlib import Path

# --- Пути -------------------------------------------------------------------
APP_DIR = Path(__file__).resolve().parent                 # пакет app/
BASE_DIR = APP_DIR.parent                                 # корень проекта
DATA_DIR = Path(os.environ.get("NEURO_DATA", str(APP_DIR / "data")))
REPORTS_DIR = DATA_DIR / "reports"                        # отчёты и XLS (служебные)
TMP_DIR = DATA_DIR / "tmp"                                # временные скрипты
DB_PATH = DATA_DIR / "memory.db"                          # RAG-память
XLS_PATH = REPORTS_DIR / "rabochiy_otchet.xlsx"           # рабочая таблица

for _d in (DATA_DIR, REPORTS_DIR, TMP_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --- Сервер -------------------------------------------------------------------
HOST = os.environ.get("NEURO_HOST", "127.0.0.1")          # локально; 0.0.0.0 только для демо
PORT = int(os.environ.get("NEURO_PORT", "8000"))
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")

# Демо-режим: интерфейс работает без Ollama (заглушки вместо моделей)
DEMO_MODE = os.environ.get("NEURO_DEMO", "0") == "1"

# --- Безопасность ---------------------------------------------------------------
# Белый список: ЕДИНСТВЕННОЕ место, где агенты могут трогать файлы.
# Несколько папок — через ";" (NEURO_WHITELIST="D:\Проекты;D:\Docs")
WHITELIST = [
    Path(p.strip())
    for p in os.environ.get("NEURO_WHITELIST", r"D:\Проекты").split(";")
    if p.strip()
]

# --- Модели (роли -> имена в Ollama) ---------------------------------------------
MODELS = {
    "orchestrator": os.environ.get("MODEL_ORCH", "qwen2.5:14b-instruct-q4_K_M"),
    "coder":        os.environ.get("MODEL_CODER", "qwen2.5-coder:7b-instruct-q4_K_M"),
    "researcher":   os.environ.get("MODEL_RES", "llama3.1:8b-instruct-q4_K_M"),
    "files":        os.environ.get("MODEL_FILES", "gemma3:4b-it-q4_K_M"),
    "executor":     os.environ.get("MODEL_EXEC", "llama3.2:3b-instruct-q4_K_M"),
    "embed":        os.environ.get("MODEL_EMBED", "nomic-embed-text:latest"),
}
WORKER_ROLES = ["coder", "researcher", "files", "executor"]

# --- Память RAM: оркестратор живёт постоянно, воркеры по требованию ---------------
MAX_LOADED_WORKERS = int(os.environ.get("NEURO_MAX_WORKERS", "2"))
WORKER_KEEP_ALIVE = "10m"       # воркер выгружается после 10 минут простоя
ORCH_KEEP_ALIVE = "-1"          # оркестратор не выгружается

# --- Исполнение -------------------------------------------------------------------
MAX_STEPS = 5                   # максимум шагов в плане
MAX_REACT_ITERS = 6             # максимум ходов воркера внутри шага
APPROVAL_TIMEOUT = 300          # секунд ждём подтверждение пользователя
SHELL_TIMEOUT = 90              # секунд на команду оболочки
BROWSER_TIMEOUT = 90            # секунд на действие браузера
