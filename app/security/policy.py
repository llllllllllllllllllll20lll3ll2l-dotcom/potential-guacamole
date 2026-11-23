"""Политика безопасности: белый/чёрный списки путей и классификация опасных операций.

Правила (согласованы с пользователем):
* файлы — только внутри WHITELIST (D:\\Проекты); системные пути запрещены безусловно;
* опасные действия (удаление, запись, скачивание, опасные команды) — только после
  подтверждения пользователем;
* всё остальное (чтение, поиск, анализ) — свободно внутри белого списка.
"""
import re
from pathlib import Path

from .. import config

# --- Чёрный список: системные пути, куда агент не смотрит НИКОГДА -----------------
BLACKLIST_PAT = [
    r"^C:\\Windows", r"\\System32", r"\\SysWOW64", r"^C:\\Program",
    r"^C:\\ProgramData", r"\\AppData", r"\$RECYCLE", r"^C:\\Users\\[^\\]+\\NTUSER",
    r"^/etc", r"^/usr", r"^/bin", r"^/sbin", r"^/boot", r"^/root", r"^/proc", r"^/sys",
]

# --- Опасные команды оболочки (требуют подтверждения) ------------------------------
DANGER_SHELL = [
    r"\bdel\b", r"\berase\b", r"\brmdir\b", r"\brd\b\s+/s", r"\brm\b",
    r"remove-item", r"\bformat\b", r"diskpart", r"\bshutdown\b", r"\breboot\b",
    r"reg\s+(add|delete|load)", r"regedit", r"\bsetx\b", r"taskkill",
    r"\bnet\s+(stop|start|user)", r"\bsc\s+(delete|config|create)",
    r"-encodedcommand", r"\bcurl\b", r"\bwget\b", r"bitsadmin", r"certutil",
    r"\btakeown\b", r"\bicacls\b", r"\battrib\b", r"\bsfc\b", r"\bchkdsk\b",
    r"bcdedit", r"\bwmic\b", r"\bmklink\b", r"invoke-webrequest",
    r"\bmove\b\s+.*[A-Za-z]:\\", r"\bren\b\s+.*C:\\",
]

# --- Опасные конструкции в Python-коде (требуют подтверждения) ---------------------
DANGER_CODE = [
    "os.remove", "os.unlink", "shutil.rmtree", "shutil.move", "os.rename",
    "os.rmdir", ".unlink(", "urlretrieve", "os.system", "subprocess",
    "shutil.copy", "webbrowser", "socket", "ctypes", "winreg",
]
WRITE_CODE_RE = re.compile(r"""open\([^)]*['"](w|wb|w\+|a|ab|a\+|x|xb)['"]""", re.I)

ABS_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s\"'|<>]*")


def _blacklisted(text: str) -> bool:
    return any(re.search(p, text, re.I) for p in BLACKLIST_PAT)


def check_path(path) -> tuple[bool, str]:
    """Путь допустим? (белый список + чёрный список)."""
    p = Path(str(path).strip().strip('"').strip("'"))
    s = str(p)
    if _blacklisted(s):
        return False, f"чёрный список системных путей: {s}"
    try:
        rp = p.resolve()
    except Exception:
        return False, f"некорректный путь: {s}"
    for w in config.WHITELIST:
        try:
            rp.relative_to(w.resolve())
            return True, ""
        except Exception:
            continue
    return False, f"вне белого списка ({'; '.join(str(w) for w in config.WHITELIST)}): {s}"


def resolve_in_whitelist(path) -> Path:
    """Относительные пути считаем относительно корня белого списка."""
    p = Path(str(path).strip().strip('"').strip("'"))
    if not p.is_absolute():
        p = config.WHITELIST[0] / p
    return p


def shell_danger(command: str) -> tuple[bool, str]:
    """Нужно ли подтверждение для команды оболочки."""
    for p in DANGER_SHELL:
        if re.search(p, command, re.I):
            return True, f"опасная команда ({p.strip(chr(92))})"
    if _blacklisted(command):
        return True, "команда затрагивает системные пути"
    for m in ABS_PATH_RE.findall(command):
        ok, reason = check_path(m.rstrip("\\/"))
        if not ok:
            return True, f"команда обращается вне белого списка: {m}"
    return False, ""


def code_danger(code: str) -> tuple[bool, str]:
    """Нужно ли подтверждение для Python-кода."""
    for p in DANGER_CODE:
        if p in code:
            return True, f"код содержит опасную конструкцию: {p}"
    if WRITE_CODE_RE.search(code):
        return True, "код записывает файлы"
    if _blacklisted(code):
        return True, "код обращается к системным путям"
    return False, ""


# Инструменты, которые ВСЕГДА требуют подтверждения (запись/удаление/скачивание)
ALWAYS_CONFIRM = {
    "write_file", "make_dir", "delete_path", "move_path",
    "browser_download",
}
