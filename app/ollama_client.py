"""Клиент Ollama: чат, эмбеддинги, управление загрузкой моделей в RAM."""
import json
import random

import httpx

from . import config

_TIMEOUT = httpx.Timeout(900.0, connect=10.0)
_ROLE_BY_MODEL = {v: k for k, v in config.MODELS.items()}


async def _post(path: str, payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = await c.post(config.OLLAMA_URL + path, json=payload)
        if r.status_code >= 400:
            # показываем настоящую причину ошибки Ollama, а не голый код HTTP
            raise RuntimeError(f"Ollama {r.status_code}: {r.text[:300]}")
        return r.json()


async def alive() -> bool:
    if config.DEMO_MODE:
        return True
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as c:
            r = await c.get(config.OLLAMA_URL + "/api/tags")
            return r.status_code == 200
    except Exception:
        return False


async def loaded_models() -> list[str]:
    """Имена моделей, которые сейчас в памяти (ollama ps)."""
    if config.DEMO_MODE:
        return [config.MODELS["orchestrator"]]
    try:
        data = await _post("/api/ps", {})
        return [m.get("name", "") for m in data.get("models", [])]
    except Exception:
        return []


async def chat(role: str, messages: list[dict], keep_alive: str | int | None = None) -> str:
    model = config.MODELS.get(role, role)
    if config.DEMO_MODE:
        return _demo_chat(model, messages)
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2, "num_ctx": 8192},
    }
    if keep_alive is not None:
        payload["keep_alive"] = keep_alive
    try:
        data = await _post("/api/chat", payload)
    except RuntimeError:
        # запасной минимальный запрос: вдруг сборка Ollama не любит format/keep_alive
        data = await _post("/api/chat",
                           {"model": model, "messages": messages, "stream": False})
    return data["message"]["content"]


async def embed(text: str) -> list[float]:
    if config.DEMO_MODE:
        random.seed(hash(text) % (2**31))
        v = [random.gauss(0, 1) for _ in range(64)]
        n = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / n for x in v]
    data = await _post("/api/embeddings", {"model": config.MODELS["embed"], "prompt": text})
    return data.get("embedding", [])


async def preload(role: str, keep_alive: str | int) -> None:
    """Загрузить модель в память (пустой запрос)."""
    if config.DEMO_MODE:
        return
    model = config.MODELS.get(role, role)
    await _post("/api/generate", {"model": model, "prompt": "", "keep_alive": keep_alive})


async def unload(role: str) -> None:
    if config.DEMO_MODE:
        return
    model = config.MODELS.get(role, role)
    try:
        await _post("/api/generate", {"model": model, "prompt": "", "keep_alive": 0})
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Демо-режим: заглушки вместо моделей, чтобы интерфейс можно было пощупать
# без Ollama. На боевом ПК (NEURO_DEMO=0) этот код не используется.
# ---------------------------------------------------------------------------
def _demo_chat(model: str, messages: list[dict]) -> str:
    role = _ROLE_BY_MODEL.get(model, "executor")
    tool_results = [m for m in messages
                    if m["role"] == "user" and m["content"].startswith("Результат инструмента")]
    if role == "orchestrator":
        if not any("РЕЗУЛЬТАТЫ ШАГОВ" in m["content"] for m in messages):
            return json.dumps({
                "comment": "Принял задачу (демо-режим). Составляю план из двух шагов.",
                "plan": [
                    {"agent": "researcher",
                     "task": "Найти в интернете 3 коротких факта по теме задачи"},
                    {"agent": "files",
                     "task": "Сохранить краткую сводку найденного в файл otchet.txt"},
                ],
            }, ensure_ascii=False)
        return json.dumps({
            "status": "done",
            "result": "Демо-задача выполнена: исследование проведено, файл сохранён.",
            "report": ("ДЕМОНСТРАЦИОННЫЙ ОТЧЁТ О РАБОТЕ\n"
                       "Шаг 1 — Исследователь: открыл браузер, выполнил поиск, собрал факты.\n"
                       "Шаг 2 — Файловый агент: создал файл otchet.txt в белой папке.\n"
                       "Замечаний нет."),
        }, ensure_ascii=False)
    if role == "researcher":
        if not tool_results:
            return json.dumps({
                "thought": "Нужно поискать информацию в интернете.",
                "action": {"tool": "browser_search",
                           "args": {"query": "как обучать локальные нейросети"}},
            }, ensure_ascii=False)
        return json.dumps({
            "thought": "Данных достаточно.",
            "action": {"tool": "final", "args": {"answer":
                       "Найдено (демо): 1) локальные модели работают без интернета; "
                       "2) Ollama управляет загрузкой моделей; 3) RAG даёт память."}},
        }, ensure_ascii=False)
    if role == "files":
        if not tool_results:
            return json.dumps({
                "thought": "Сохраняю сводку в файл.",
                "action": {"tool": "write_file",
                           "args": {"path": "otchet.txt",
                                    "content": "Краткая сводка: демонстрационные данные.\n"}},
            }, ensure_ascii=False)
        return json.dumps({
            "thought": "Готово.",
            "action": {"tool": "final", "args": {"answer": "Файл otchet.txt сохранён."}},
        }, ensure_ascii=False)
    if role == "coder":
        return json.dumps({"thought": "Код не требуется.",
                           "action": {"tool": "final",
                                      "args": {"answer": "Код не потребовался (демо)."}}},
                          ensure_ascii=False)
    return json.dumps({"thought": "Выполняю.",
                       "action": {"tool": "final",
                                  "args": {"answer": "Команда выполнена (демо)."}}},
                      ensure_ascii=False)
