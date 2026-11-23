"""Чат с LLM через OpenAI-совместимый API (Ollama, OpenRouter, DeepSeek, OpenAI...)."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    sys.exit("Не установлен пакет openai: pip install -r requirements.txt")

from .tools import SYSTEM_PROMPT, dumps, run_tool, tools

DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_MODEL = "qwen2.5:7b-instruct"


def _config() -> tuple[str, str, str]:
    base_url = os.getenv("GMA_BASE_URL", DEFAULT_BASE_URL)
    api_key = os.getenv("GMA_API_KEY", "ollama")
    model = os.getenv("GMA_MODEL", DEFAULT_MODEL)
    return base_url, api_key, model


def _assistant_payload(message: Any) -> dict[str, Any]:
    """Приводит ответ SDK к словарю, который понимает Ollama/OpenAI."""
    payload: dict[str, Any] = {"role": "assistant", "content": message.content or ""}
    calls = getattr(message, "tool_calls", None)
    if calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                },
            }
            for call in calls
        ]
    return payload


def chat() -> None:
    base_url, api_key, model = _config()
    client = OpenAI(base_url=base_url, api_key=api_key)

    from .memory import MemorySession

    session = MemorySession()
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]

    print("=" * 62)
    print(" Game Memory Agent — правка значений в офлайн-играх")
    print("=" * 62)
    print(f" Модель:  {model}")
    print(f" API:     {base_url}")
    print(" Команды: exit — выход, /status — состояние, /reset — очистить диалог")
    print(" Пример:  «подключись к game.exe и найди 100 золота»")
    print("-" * 62)

    while True:
        try:
            user = input("\nВы> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nПока!")
            break

        if not user:
            continue
        if user.lower() in ("exit", "quit", "выход", "q"):
            break
        if user == "/status":
            print(json.dumps(session.status(), ensure_ascii=False, indent=2))
            continue
        if user == "/reset":
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            print("Диалог очищен.")
            continue

        messages.append({"role": "user", "content": user})
        try:
            _run_turn(client, model, messages, session)
        except KeyboardInterrupt:
            print("\nПрервано.")
        except Exception as error:  # noqa: BLE001 — показываем любую ошибку пользователю
            print(f"[!] {type(error).__name__}: {error}")


def _run_turn(
    client: OpenAI, model: str, messages: list[dict[str, Any]], session: Any
) -> None:
    """Один ход: модель может несколько раз вызвать инструменты."""
    for _ in range(15):
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools(),
            temperature=0.2,
        )
        message = response.choices[0].message
        messages.append(_assistant_payload(message))

        if not getattr(message, "tool_calls", None):
            if message.content:
                print(f"\nИИ> {message.content}")
            return

        for call in message.tool_calls:
            raw_args = call.function.arguments or "{}"
            print(f"   ⚙ {call.function.name}({raw_args[:200]})")
            try:
                args = json.loads(raw_args)
                result = run_tool(session, call.function.name, args)
            except Exception as error:  # noqa: BLE001 — ошибка уходит модели
                result = {"error": f"{type(error).__name__}: {error}"}
            print(f"   ← {dumps(result)[:300]}")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": dumps(result),
                }
            )

    print("[!] Слишком много шагов подряд — остановился.")
