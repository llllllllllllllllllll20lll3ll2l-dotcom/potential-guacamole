"""НейроКоманда: веб-сервер, WebSocket-мост в интерфейс, запуск системы."""
import asyncio
import platform
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, ollama_client
from .agents import runner
from .memory import rag
from .reports import xls
from .security import approvals

_clients: set[asyncio.Queue] = set()


async def broadcast(msg: dict):
    dead = []
    for q in _clients:
        try:
            q.put_nowait(msg)
        except Exception:
            dead.append(q)
    for q in dead:
        _clients.discard(q)


async def warmup():
    """Ждём Ollama и держим Оркестратора в памяти."""
    for _ in range(60):
        if await ollama_client.alive():
            break
        await asyncio.sleep(3)
    if config.DEMO_MODE:
        await runner.log("🎭 Демо-режим: модели заменены заглушками.", "warn")
        return
    if not await ollama_client.alive():
        await runner.log("⚠️ Ollama не отвечает на " + config.OLLAMA_URL, "warn")
        return
    await runner.log("🧠 Ollama на связи. Загружаю Оркестратора в RAM…", "info")
    try:
        await ollama_client.preload("orchestrator", config.ORCH_KEEP_ALIVE)
        await runner.log("✅ Оркестратор в памяти. Система готова.", "info")
    except Exception as e:
        await runner.log(f"⚠️ Не удалось предзагрузить Оркестратора: {e}", "warn")


@asynccontextmanager
async def lifespan(app: FastAPI):
    rag.init()
    xls.ensure_xls()
    approvals.set_broadcast(broadcast)
    runner.set_broadcast(broadcast)
    t = asyncio.create_task(warmup())
    if platform.system() == "Windows" and not config.DEMO_MODE:
        webbrowser.open(f"http://127.0.0.1:{config.PORT}")
    yield
    t.cancel()
    from .tools import browser_tools
    browser_tools.manager.stop()


app = FastAPI(title="НейроКоманда", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


@app.get("/")
async def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/api/health")
async def health():
    return {
        "ollama": await ollama_client.alive(),
        "demo": config.DEMO_MODE,
        "models": config.MODELS,
        "memory": rag.count(),
        "whitelist": [str(w) for w in config.WHITELIST],
        "loaded": await ollama_client.loaded_models(),
    }


class TaskIn(BaseModel):
    text: str


@app.post("/api/task")
async def api_task(t: TaskIn):
    asyncio.create_task(runner.run_task(t.text.strip()))
    return {"ok": True}


class ApproveIn(BaseModel):
    id: int
    ok: bool


@app.post("/api/approve")
async def api_approve(a: ApproveIn):
    return {"ok": approvals.decide(a.id, a.ok)}


@app.post("/api/memory/clear")
async def api_memory_clear():
    n = rag.clear()
    await broadcast({"type": "memory_cleared", "count": 0})
    await runner.log(f"🗑 Память очищена (удалено записей: {n})", "warn")
    return {"ok": True, "removed": n}


@app.get("/api/reports/xls")
async def get_xls():
    xls.ensure_xls()
    return FileResponse(config.XLS_PATH,
                        filename="rabochiy_otchet.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/reports/list")
async def reports_list():
    files = sorted(config.REPORTS_DIR.glob("otchet_*.txt"), reverse=True)
    return [{"name": f.name, "size": f.stat().st_size} for f in files[:30]]


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    _clients.add(q)
    try:
        await websocket.send_json({
            "type": "hello",
            "health": await health(),
        })

        async def pump():
            while True:
                msg = await q.get()
                await websocket.send_json(msg)

        pump_task = asyncio.create_task(pump())
        while True:
            data = await websocket.receive_json()
            kind = data.get("type")
            if kind == "task" and str(data.get("text", "")).strip():
                asyncio.create_task(runner.run_task(str(data["text"]).strip()))
            elif kind == "approve":
                approvals.decide(int(data.get("id", -1)), bool(data.get("ok")))
            elif kind == "memory_clear":
                n = rag.clear()
                await broadcast({"type": "memory_cleared", "count": 0})
                await runner.log(f"🗑 Память очищена (удалено записей: {n})", "warn")
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _clients.discard(q)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT)
