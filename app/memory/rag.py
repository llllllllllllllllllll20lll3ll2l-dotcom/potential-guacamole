"""RAG-память: SQLite + векторы nomic-embed-text, поиск по смыслу, кнопка очистки."""
import sqlite3
import time

import numpy as np

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


async def add(text: str, meta: str = "") -> None:
    text = (text or "").strip()
    if not text:
        return
    vec = await ollama_client.embed(text[:2000])
    if not vec:
        return
    blob = np.asarray(vec, dtype=np.float32).tobytes()
    with _conn() as c:
        c.execute("INSERT INTO chunks(ts, meta, text, vec) VALUES (?,?,?,?)",
                  (time.time(), meta, text[:4000], blob))


def search_sync(query_vec: list[float], k: int = 4) -> list[str]:
    q = np.asarray(query_vec, dtype=np.float32)
    qn = q / (np.linalg.norm(q) or 1.0)
    rows = []
    with _conn() as c:
        for text, blob in c.execute("SELECT text, vec FROM chunks"):
            v = np.frombuffer(blob, dtype=np.float32)
            vn = v / (np.linalg.norm(v) or 1.0)
            rows.append((float(np.dot(qn, vn)), text))
    rows.sort(key=lambda x: -x[0])
    return [t for s, t in rows[:k] if s > 0.15]


async def search(query: str, k: int = 4) -> str:
    """Текст воспоминаний, релевантных запросу (пустая строка — если пусто)."""
    if count() == 0:
        return ""
    vec = await ollama_client.embed(query[:2000])
    if not vec:
        return ""
    hits = search_sync(vec, k)
    return "\n---\n".join(hits)


def clear() -> int:
    n = count()
    with _conn() as c:
        c.execute("DELETE FROM chunks")
    return n
