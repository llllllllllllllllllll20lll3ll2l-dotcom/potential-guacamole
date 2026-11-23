"""Исполнение: команды Windows и Python-скрипты (внутри белой папки)."""
import subprocess
import sys
import time
from pathlib import Path

from .. import config


def _cwd() -> str | None:
    w = config.WHITELIST[0]
    return str(w) if w.exists() else None


def _trim(s: str, limit: int = 4000) -> str:
    s = s or ""
    return s if len(s) <= limit else s[:limit] + f"\n...[обрезано, всего {len(s)}]"


def run_shell(command: str, timeout: int | None = None) -> str:
    """Команда оболочки; cwd = белая папка, вывод обрезается."""
    try:
        r = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=timeout or config.SHELL_TIMEOUT, cwd=_cwd(),
            errors="replace",
        )
        out = _trim(r.stdout)
        err = _trim(r.stderr, 2000)
        parts = [f"Код возврата: {r.returncode}"]
        if out.strip():
            parts.append(f"ВЫВОД:\n{out}")
        if err.strip():
            parts.append(f"ОШИБКИ:\n{err}")
        return "\n".join(parts)
    except subprocess.TimeoutExpired:
        return "⛔ Команда не уложилась во время и остановлена."
    except Exception as e:
        return f"Ошибка запуска: {e}"


def run_python(code: str, timeout: int | None = None) -> str:
    """Python-скрипт во временном файле, cwd = белая папка."""
    script = config.TMP_DIR / f"script_{int(time.time() * 1000)}.py"
    script.write_text(code, encoding="utf-8")
    try:
        r = subprocess.run(
            [sys.executable, str(script)], capture_output=True, text=True,
            timeout=timeout or config.SHELL_TIMEOUT, cwd=_cwd(), errors="replace",
        )
        out = _trim(r.stdout)
        err = _trim(r.stderr, 2000)
        parts = [f"Код возврата: {r.returncode}"]
        if out.strip():
            parts.append(f"ВЫВОД:\n{out}")
        if err.strip():
            parts.append(f"ОШИБКИ:\n{err}")
        return "\n".join(parts)
    except subprocess.TimeoutExpired:
        return "⛔ Скрипт не уложился во время и остановлен."
    except Exception as e:
        return f"Ошибка запуска: {e}"
    finally:
        try:
            script.unlink()
        except OSError:
            pass
