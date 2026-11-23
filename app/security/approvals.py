"""Очередь подтверждений: опасное действие ждёт решения пользователя."""
import asyncio
import itertools
import json

from .. import config

_broadcast = None          # функция рассылки сообщений в веб-интерфейс (ставит main)
_pending: dict[int, dict] = {}
_ids = itertools.count(1)


def set_broadcast(fn):
    global _broadcast
    _broadcast = fn


def _safe_args(args) -> str:
    try:
        return json.dumps(args, ensure_ascii=False, indent=1)[:800]
    except Exception:
        return str(args)[:800]


async def request(desc: str, tool: str, args: dict) -> bool:
    """Попросить подтверждение. True = пользователь разрешил."""
    aid = next(_ids)
    ev = asyncio.Event()
    _pending[aid] = {"event": ev, "result": None}
    if _broadcast:
        await _broadcast({
            "type": "approval", "id": aid, "desc": desc,
            "tool": tool, "args": _safe_args(args),
        })
    try:
        await asyncio.wait_for(ev.wait(), config.APPROVAL_TIMEOUT)
        result = _pending.get(aid, {}).get("result", False)
    except asyncio.TimeoutError:
        result = False
    _pending.pop(aid, None)
    if _broadcast:
        await _broadcast({"type": "approval_done", "id": aid, "ok": bool(result)})
    return bool(result)


def decide(aid: int, ok: bool) -> bool:
    item = _pending.get(aid)
    if not item:
        return False
    item["result"] = bool(ok)
    item["event"].set()
    return True


def pending_ids() -> list[int]:
    return list(_pending.keys())
