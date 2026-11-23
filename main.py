"""Точка входа: `python main.py` — чат с LLM, `python main.py --cli` — без LLM."""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="game-memory-agent",
        description="ИИ-ассистент для правки значений в памяти офлайн-игр (Windows).",
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="режим без LLM: команды вручную (attach, scan, next, write...)",
    )
    args = parser.parse_args()

    if sys.platform != "win32":
        print("Внимание: доступ к памяти процесса работает только на Windows.")
        print("На этой ОС можно проверить только тесты: python -m pytest -q")
        sys.exit(1)

    if args.cli:
        from game_memory_agent.cli import repl

        repl()
    else:
        from game_memory_agent.agent import chat

        chat()


if __name__ == "__main__":
    main()
