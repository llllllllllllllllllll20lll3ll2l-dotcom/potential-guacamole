"""Инструменты (function calling) для LLM: схемы + диспетчер.

Модуль не импортирует memory.py на верхнем уровне, чтобы схемы можно было
тестировать на любой ОС — реальный доступ к памяти нужен только при вызове.
"""
from __future__ import annotations

import json
from typing import Any

from .core import SCAN_DTYPES, parse_address

SYSTEM_PROMPT = """Ты — оператор редактора памяти для офлайн-игр на Windows. Работаешь строго через инструменты.

Порядок работы:
1. `list_processes` → найди exe игры → `attach` (name или pid). Если процессов несколько, спроси pid у пользователя.
2. `scan(dtype, value)` — первое сканирование. Спроси у пользователя точное значение на экране (например золото 100, HP 75.5).
3. Попроси пользователя ИЗМЕНИТЬ значение в игре (потратить/получить) и назвать новое → `next_scan`. Повторяй, пока не останется 1–5 адресов.
4. Перед первой записью покажи найденные адреса и спроси подтверждение. Затем `write` (или `freeze`, чтобы значение не менялось).
5. Если значение дробное — используй f32/f64. Если целое не находится — попробуй u32/i64/f32/f64 и скажи пользователю, что типы отличаются.

Правила:
- Никогда не придумывай адреса и pid — только из результатов инструментов.
- Не работай с онлайн-играми и играми с античитом (EAC, BattlEye, Vanguard): откажи и объясни про бан.
- Если пользователь просит что-то опасное для системы (не игры), откажи.
- Отвечай по-русски, кратко. Адреса показывай в hex.
- После перезапуска игры адреса меняются: подключайся заново и сканируй заново.
"""

_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_processes",
            "description": "Список запущенных процессов (pid, имя exe). Первый шаг — найти игру.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "attach",
            "description": "Подключиться к процессу игры по имени exe или pid. Сбрасывает прошлые результаты сканирования.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Например 'game.exe'"},
                    "pid": {"type": "integer", "description": "PID процесса"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "status",
            "description": "Текущее состояние: подключение, число кандидатов, замороженные адреса.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scan",
            "description": "Первое сканирование памяти по значению. Возвращает адреса-кандидаты.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dtype": {
                        "type": "string",
                        "enum": SCAN_DTYPES,
                        "description": "Тип данных. Целое — i32, дробное — f32/f64.",
                    },
                    "value": {
                        "type": ["number", "string"],
                        "description": "Искомое значение (число; для string — текст, для bytes — hex).",
                    },
                },
                "required": ["dtype", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "next_scan",
            "description": "Уточнить прошлый поиск: проверяет только адреса-кандидаты с новым значением.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dtype": {"type": "string", "enum": SCAN_DTYPES},
                    "value": {"type": ["number", "string"]},
                    "tolerance": {
                        "type": "number",
                        "description": "Допуск для float (например 0.01). По умолчанию 0 — точное совпадение.",
                    },
                },
                "required": ["dtype", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "aob_scan",
            "description": "Поиск по байтовому паттерну с wildcard: '48 8B ?? 90'. Для стабильных сигнатур.",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Прочитать значение по адресу.",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": ["integer", "string"],
                        "description": "Адрес из результатов scan/next_scan (0x...).",
                    },
                    "dtype": {"type": "string", "enum": SCAN_DTYPES},
                },
                "required": ["address", "dtype"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write",
            "description": "Записать значение по адресу. Применять только после подтверждения пользователя.",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {"type": ["integer", "string"]},
                    "dtype": {"type": "string", "enum": SCAN_DTYPES},
                    "value": {"type": ["number", "string"]},
                },
                "required": ["address", "dtype", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "freeze",
            "description": "Заморозить значение: фоновый поток каждые 100 мс перезаписывает его, игра не может изменить.",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {"type": ["integer", "string"]},
                    "dtype": {"type": "string", "enum": SCAN_DTYPES},
                    "value": {"type": ["number", "string"]},
                },
                "required": ["address", "dtype", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "unfreeze",
            "description": "Снять заморозку адреса (или всех адресов, если address не указан).",
            "parameters": {
                "type": "object",
                "properties": {"address": {"type": ["integer", "string"]}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reset_scan",
            "description": "Очистить результаты сканирования (начать поиск заново).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


def tools() -> list[dict[str, Any]]:
    """Схемы инструментов для OpenAI-совместимого API."""
    return _TOOLS


def run_tool(session: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Выполняет один инструмент. Возвращаемое значение уходит обратно модели."""
    args = args or {}
    if name == "list_processes":
        from .memory import list_processes

        return {"processes": list_processes()}

    if name == "attach":
        return session.attach(
            name=args.get("name"), pid=args.get("pid")
        )

    if name == "status":
        return session.status()

    if name == "scan":
        return session.scan(args["value"], args["dtype"])

    if name == "next_scan":
        return session.next_scan(
            args["value"], args["dtype"], float(args.get("tolerance") or 0.0)
        )

    if name == "aob_scan":
        return session.aob_scan(str(args["pattern"]))

    if name == "read":
        return {
            "address": hex(parse_address(args["address"])),
            "value": session.read_value(parse_address(args["address"]), args["dtype"]),
            "dtype": args["dtype"],
        }

    if name == "write":
        address = parse_address(args["address"])
        return {
            "address": hex(address),
            "value": session.write_value(address, args["value"], args["dtype"]),
            "dtype": args["dtype"],
        }

    if name == "freeze":
        return session.freeze(
            parse_address(args["address"]), args["value"], args["dtype"]
        )

    if name == "unfreeze":
        address = args.get("address")
        return session.unfreeze(parse_address(address) if address is not None else None)

    if name == "reset_scan":
        return session.reset_scan()

    raise ValueError(f"Неизвестный инструмент: {name}")


def dumps(value: Any) -> str:
    """JSON для отправки результата инструменту (без ASCII-экранирования)."""
    return json.dumps(value, ensure_ascii=False, default=str)
