"""Файловые инструменты: работают ТОЛЬКО внутри белого списка."""
import shutil
from pathlib import Path

from ..security import policy

_LIST_LIMIT = 300
_READ_LIMIT = 8000


def _ok(path) -> tuple[bool, str, Path]:
    p = policy.resolve_in_whitelist(path)
    good, reason = policy.check_path(p)
    return good, reason, p


def list_dir(path: str = ".") -> str:
    good, reason, p = _ok(path)
    if not good:
        return f"⛔ Отказ: {reason}"
    if not p.exists():
        return f"Папка не существует: {p}"
    lines = []
    try:
        for i, item in enumerate(sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name))):
            if i >= _LIST_LIMIT:
                lines.append(f"... и ещё (показано {_LIST_LIMIT})")
                break
            tag = "📁" if item.is_dir() else "📄"
            size = f" ({item.stat().st_size} Б)" if item.is_file() else ""
            lines.append(f"{tag} {item.name}{size}")
    except Exception as e:
        return f"Ошибка чтения папки: {e}"
    return f"Содержимое {p}:\n" + "\n".join(lines) if lines else f"Папка {p} пуста"


def read_file(path: str, limit: int = _READ_LIMIT) -> str:
    good, reason, p = _ok(path)
    if not good:
        return f"⛔ Отказ: {reason}"
    if not p.is_file():
        return f"Файл не найден: {p}"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"Ошибка чтения: {e}"
    if len(text) > limit:
        text = text[:limit] + f"\n...[обрезано, всего {len(text)} символов]"
    return f"=== {p.name} ===\n{text}"


def write_file(path: str, content: str) -> str:
    good, reason, p = _ok(path)
    if not good:
        return f"⛔ Отказ: {reason}"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"✅ Записано: {p} ({len(content)} символов)"
    except Exception as e:
        return f"Ошибка записи: {e}"


def make_dir(path: str) -> str:
    good, reason, p = _ok(path)
    if not good:
        return f"⛔ Отказ: {reason}"
    try:
        p.mkdir(parents=True, exist_ok=True)
        return f"✅ Папка создана: {p}"
    except Exception as e:
        return f"Ошибка: {e}"


def delete_path(path: str) -> str:
    good, reason, p = _ok(path)
    if not good:
        return f"⛔ Отказ: {reason}"
    if not p.exists():
        return f"Не существует: {p}"
    try:
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        return f"✅ Удалено: {p}"
    except Exception as e:
        return f"Ошибка удаления: {e}"


def move_path(src: str, dst: str) -> str:
    good1, reason1, p1 = _ok(src)
    good2, reason2, p2 = _ok(dst)
    if not good1:
        return f"⛔ Отказ (источник): {reason1}"
    if not good2:
        return f"⛔ Отказ (куда): {reason2}"
    try:
        p2.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p1), str(p2))
        return f"✅ Перемещено: {p1} → {p2}"
    except Exception as e:
        return f"Ошибка перемещения: {e}"
