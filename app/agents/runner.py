"""Исполнитель задач: план Оркестратора → шаги воркеров → отчёт, XLS, уведомление."""
import asyncio
import json
import re
import time
from datetime import datetime

from .. import config, ollama_client
from ..memory import rag
from ..reports import xls
from ..security import approvals, policy
from ..tools import browser_tools, fs_tools, shell_tools
from . import prompts

ROLE_NAMES = {
    "orchestrator": "Оркестратор", "coder": "Программист",
    "researcher": "Исследователь", "files": "Файловый", "executor": "Исполнитель",
}

_broadcast = None
_busy = asyncio.Lock()


def set_broadcast(fn):
    global _broadcast
    _broadcast = fn


async def log(line: str, kind: str = "info"):
    ts = datetime.now().strftime("%H:%M:%S")
    if _broadcast:
        await _broadcast({"type": "log", "ts": ts, "line": line, "kind": kind})


def token_cb(role_name: str):
    """Колбэк стриминга: шлёт токены в интерфейс порциями (не чаще 4 раз/сек)."""
    state = {"last": 0.0, "buf": ""}

    async def cb(tok: str):
        state["buf"] += tok
        now = time.monotonic()
        if now - state["last"] > 0.25 or len(state["buf"]) > 150:
            state["last"] = now
            text, state["buf"] = state["buf"], ""
            if _broadcast:
                await _broadcast({"type": "token", "role": role_name, "text": text})

    async def flush():
        if state["buf"] and _broadcast:
            await _broadcast({"type": "token", "role": role_name, "text": state["buf"]})
        state["buf"] = ""
        if _broadcast:
            await _broadcast({"type": "token_end", "role": role_name})

    return cb, flush


def parse_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Диспетчер инструментов
# ---------------------------------------------------------------------------
_BROWSER_ARGS = {
    "search": ["query"], "open": ["url"], "click": ["target"],
    "type": ["target", "text", "submit"], "text": [], "tabs": [],
    "new_tab": ["url"], "close_tab": ["index"],
}
_BROWSER_FN = {
    "search": "search", "open": "open_url", "click": "click", "type": "type_text",
    "text": "page_text", "tabs": "tabs", "new_tab": "new_tab", "close_tab": "close_tab",
}
_FS_FN = {
    "list_dir": fs_tools.list_dir, "read_file": fs_tools.read_file,
    "write_file": fs_tools.write_file, "make_dir": fs_tools.make_dir,
    "delete_path": fs_tools.delete_path, "move_path": fs_tools.move_path,
}


async def exec_tool(role: str, name: str, args: dict) -> tuple[str, str | None]:
    """Выполнить инструмент; вернуть (наблюдение, скриншот|None)."""
    bm = browser_tools.manager
    if name.startswith("browser_"):
        act = name[len("browser_"):]
        fn_name = _BROWSER_FN.get(act)
        if not fn_name:
            return f"Неизвестный инструмент {name}", None
        fn = getattr(bm, fn_name)
        kwargs = {k: args[k] for k in _BROWSER_ARGS.get(act, []) if k in args}
        fut = bm.call(fn, **kwargs)
        res = await asyncio.to_thread(fut.result, config.BROWSER_TIMEOUT)
        return res.get("obs", ""), res.get("shot")
    if name == "run_python":
        obs = await asyncio.to_thread(shell_tools.run_python, str(args.get("code", "")))
        return obs, None
    if name == "shell":
        obs = await asyncio.to_thread(shell_tools.run_shell, str(args.get("command", "")))
        return obs, None
    fn = _FS_FN.get(name)
    if fn:
        kwargs = {k: args[k] for k in ("path", "content", "src", "dst") if k in args}
        obs = await asyncio.to_thread(lambda: fn(**kwargs))
        return obs, None
    return f"Неизвестный инструмент: {name}", None


def tool_danger(role: str, name: str, args: dict) -> tuple[bool, str]:
    if name in policy.ALWAYS_CONFIRM:
        return True, "запись/создание/удаление/перемещение файлов"
    if name == "shell":
        return policy.shell_danger(str(args.get("command", "")))
    if name == "run_python":
        return policy.code_danger(str(args.get("code", "")))
    return False, ""


# ---------------------------------------------------------------------------
# Менеджер RAM: оркестратор постоянно, воркеры по требованию (не больше N)
# ---------------------------------------------------------------------------
async def ensure_worker(role: str):
    if config.DEMO_MODE:
        return
    loaded = await ollama_client.loaded_models()
    workers = [r for r in config.WORKER_ROLES if config.MODELS[r] in loaded]
    if role in workers:
        return
    while len(workers) >= config.MAX_LOADED_WORKERS:
        victim = next((w for w in workers if w != role), workers[0])
        await log(f"🧠 RAM: выгружаю воркера «{ROLE_NAMES[victim]}»", "info")
        await ollama_client.unload(victim)
        workers = [w for w in workers if w != victim]
    await log(f"🧠 RAM: загружаю модель {config.MODELS[role]} ({ROLE_NAMES[role]})…", "info")
    await ollama_client.preload(role, config.WORKER_KEEP_ALIVE)


# ---------------------------------------------------------------------------
# Шаг воркера (цикл ReAct)
# ---------------------------------------------------------------------------
async def run_step(role: str, task_text: str, memory_ctx: str) -> str:
    await ensure_worker(role)
    msgs = [
        {"role": "system",
         "content": prompts.WORKER_SYSTEM[role] + "\n" + prompts.tools_block(role)},
        {"role": "user",
         "content": (f"Задача шага: {task_text}\n"
                     + (f"Воспоминания из памяти:\n{memory_ctx}\n" if memory_ctx else "")
                     + "Действуй. Отвечай только JSON.")},
    ]
    answer = ""
    last_obs = ""
    for it in range(config.MAX_REACT_ITERS):
        if it == config.MAX_REACT_ITERS - 1:
            msgs.append({"role": "user",
                         "content": ("ПОСЛЕДНИЙ ХОД: инструменты вызывать нельзя. "
                                     "Верни final с answer по уже собранным данным.")})
        cb, flush = token_cb(ROLE_NAMES[role])
        raw = await ollama_client.chat_stream(role, msgs,
                                              keep_alive=config.WORKER_KEEP_ALIVE,
                                              on_token=cb)
        await flush()
        data = parse_json(raw)
        if not data or "action" not in data:
            msgs.append({"role": "assistant", "content": raw or ""})
            msgs.append({"role": "user",
                         "content": "Ответь строго JSON по схеме: {\"thought\", \"action\": {\"tool\", \"args\"}}."})
            continue
        action = data.get("action") or {}
        tool = str(action.get("tool", "")).strip()
        targs = action.get("args") or {}
        thought = str(data.get("thought", "")).strip()
        if thought:
            await log(f"💭 {ROLE_NAMES[role]}: {thought[:180]}", "thought")
        if tool == "final":
            answer = str(targs.get("answer", "")).strip() or raw[:800]
            break
        danger, reason = tool_danger(role, tool, targs)
        if danger:
            await log(f"🔒 Нужно подтверждение: {tool} ({reason})", "warn")
            ok = await approvals.request(
                f"{ROLE_NAMES[role]} хочет выполнить «{tool}»: {reason}", tool, targs)
            if not ok:
                obs, shot = ("⛔ Пользователь ЗАПРЕТИЛ это действие. Выбери другой способ.", None)
                await log(f"🚫 Действие {tool} отклонено пользователем", "warn")
            else:
                await log(f"✅ Действие {tool} разрешено пользователем", "info")
                obs, shot = await exec_tool(role, tool, targs)
        else:
            obs, shot = await exec_tool(role, tool, targs)
        if obs and not obs.startswith("⛔"):
            last_obs = obs
        if _broadcast:
            await _broadcast({"type": "action", "role": ROLE_NAMES[role],
                              "tool": tool, "obs": obs[:700], "shot": shot})
        await log(f"🔧 {ROLE_NAMES[role]} → {tool}: {obs[:180]}", "action")
        msgs.append({"role": "assistant", "content": raw})
        msgs.append({"role": "user", "content": "Результат инструмента:\n" + obs[:6000]})
    else:
        # лимит ходов: не теряем собранное, отдаём последние данные как результат
        answer = answer or ("Шаг прерван по лимиту ходов. Последние полученные данные:\n"
                            + last_obs[:1500])
    return answer


# ---------------------------------------------------------------------------
# Задача целиком
# ---------------------------------------------------------------------------
async def finish(task: str, report: str, steps_log: list[str], status: str,
                 result: str, t0: float):
    path = xls.write_text_report(task, report, steps_log)
    xls.append_row(task, "Оркестратор", status, result, time.time() - t0)
    try:
        await rag.add(f"Задача: {task}\nИтог: {result}", meta="task")
    except Exception:
        pass
    if _broadcast:
        await _broadcast({"type": "report", "text": report, "file": str(path),
                          "status": status, "result": result})
        await _broadcast({"type": "notify", "title": "НейроКоманда: задача завершена",
                          "body": result[:180]})
        await _broadcast({"type": "task_done", "status": status})
    await log(f"🏁 Готово ({status}): {result[:180]}", "task")


async def run_task(task: str):
    if _busy.locked():
        if _broadcast:
            await _broadcast({"type": "log", "ts": datetime.now().strftime("%H:%M:%S"),
                              "line": "⏳ Уже выполняется задача — подожди завершения.",
                              "kind": "warn"})
        return
    async with _busy:
        t0 = time.time()
        steps_log: list[str] = []
        await log(f"📨 Задача: {task}", "task")
        if _broadcast:
            await _broadcast({"type": "task_started", "task": task})
        try:
            mem = ""
            try:
                mem = await rag.search(task, 3)
            except Exception:
                mem = ""
            cb, flush = token_cb("Оркестратор")
            raw = await ollama_client.chat_stream(
                "orchestrator",
                [{"role": "system", "content": prompts.ORCH_PLANNER},
                 {"role": "user", "content": task +
                  (f"\nВоспоминания из памяти (RAG):\n{mem}" if mem else "")}],
                keep_alive=config.ORCH_KEEP_ALIVE, on_token=cb)
            await flush()
            plan_data = parse_json(raw) or {}
            comment = str(plan_data.get("comment", ""))
            plan = [s for s in (plan_data.get("plan") or []) if isinstance(s, dict)]
            if comment:
                await log(f"🧠 Оркестратор: {comment[:200]}", "orch")
            if _broadcast:
                await _broadcast({"type": "plan", "comment": comment, "plan": plan})

            if not plan:
                result = comment or "Задача не требует действий."
                steps_log.append(f"Оркестратор ответил напрямую: {result[:200]}")
                await finish(task, result, steps_log, "done", result, t0)
                return

            results = []
            for i, step in enumerate(plan[:config.MAX_STEPS], 1):
                role = step.get("agent")
                role = role if role in config.WORKER_ROLES else "executor"
                stask = str(step.get("task", ""))
                await log(f"▶ Шаг {i}/{len(plan)}: {ROLE_NAMES[role]} — {stask[:150]}", "step")
                if _broadcast:
                    await _broadcast({"type": "step_start", "i": i,
                                      "role": ROLE_NAMES[role], "task": stask})
                ts = time.time()
                try:
                    ans = await run_step(role, stask, mem)
                    status = "done"
                except Exception as e:
                    ans, status = f"Ошибка шага: {e}", "fail"
                    await log(f"⚠️ {ans}", "warn")
                dur = time.time() - ts
                results.append((i, role, stask, status, ans))
                steps_log.append(f"Шаг {i} [{ROLE_NAMES[role]}] {stask} → {status}: {ans[:300]}")
                xls.append_row(stask, ROLE_NAMES[role], status, ans, dur)
                if _broadcast:
                    await _broadcast({"type": "step_done", "i": i,
                                      "role": ROLE_NAMES[role], "status": status,
                                      "result": ans[:900]})
                await log(f"✔ Шаг {i} ({ROLE_NAMES[role]}): {ans[:160]}", "step")

            cb, flush = token_cb("Оркестратор")
            raw = await ollama_client.chat_stream(
                "orchestrator",
                [{"role": "system", "content": prompts.ORCH_SUMMARIZER},
                 {"role": "user", "content":
                  f"Задача: {task}\n\nРЕЗУЛЬТАТЫ ШАГОВ:\n" +
                  "\n".join(f"{i}. [{ROLE_NAMES[r]}] {t} — {s}: {a}"
                            for i, r, t, s, a in results)}],
                keep_alive=config.ORCH_KEEP_ALIVE, on_token=cb)
            await flush()
            summ = parse_json(raw) or {"status": "partial", "result": raw[:400], "report": raw}
            await finish(task, str(summ.get("report", "")), steps_log,
                         str(summ.get("status", "done")), str(summ.get("result", "")), t0)
        except Exception as e:
            await log(f"💥 Ошибка выполнения задачи: {e}", "warn")
            try:
                xls.append_row(task, "Система", "fail", str(e), time.time() - t0)
            except Exception:
                pass
            if _broadcast:
                await _broadcast({"type": "notify", "title": "НейроКоманда: ошибка",
                                  "body": str(e)[:180]})
                await _broadcast({"type": "task_done", "status": "fail"})
