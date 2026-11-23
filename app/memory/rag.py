"""RAG-память: SQLite + векторы nomic-embed-text, поиск по смыслу, кнопка очистки.

Без внешних C-зависимостей: векторы храним как массивы float32 (модуль array),
косинус считаем на чистом Python — работает на любой версии Python.
"""
import array
import sqlite3
import time
from math import sqrt

from .. import config, ollama_client


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(config.DB_PATH)


def init() -> None:
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS chunks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL, meta TEXT, text TEXT, vec BLOB)""")


def count() -> int:
    try:
        with _conn() as c:
            return c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    except Exception:
        return 0


def _vec(blob: bytes) -> array.array:
    a = array.array("f")
    a.frombytes(blob)
    return a


def _norm(a: array.array) -> float:
    return sqrt(sum(x * x for x in a))


def _cos(a: array.array, b: array.array) -> float:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


async def add(text: str, meta: str = "") -> None:
    text = (text or "").strip()
    if not text:
        return
    vec = await ollama_client.embed(text[:2000])
    if not vec:
        return
    blob = array.array("f", vec).tobytes()
    with _conn() as c:
        c.execute("INSERT INTO chunks(ts, meta, text, vec) VALUES (?,?,?,?)",
                  (time.time(), meta, text[:4000], blob))


def search_sync(query_vec: list[float], k: int = 4) -> list[str]:
    q = array.array("f", query_vec)
    rows = []
    with _conn() as c:
        for text, blob in c.execute("SELECT text, vec FROM chunks"):
            rows.append((_cos(q, _vec(blob)), text))
    rows.sort(key=lambda x: -x[0])
    return [t for s, t in rows[:k] if s > 0.15]


async def search(query: str, k: int = 4) -> str:
    """Текст воспоминаний, релевантных запросу (пустая строка — если пусто)."""
    if count() == 0:
        return ""
    vec = await ollama_client.embed(query[:2000])
    if not vec:
        return ""
    return "\n---\n".join(search_sync(vec, k))


def clear() -> int:
    n = count()
    with _conn() as c:
        c.execute("DELETE FROM chunks")
    return n
