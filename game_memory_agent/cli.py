"""CLI без LLM: команды вручную. Удобно для отладки и как запасной вариант."""
from __future__ import annotations

from .core import parse_address

HELP = """Команды:
  attach <имя|pid>          подключиться к процессу (например: attach game.exe)
  scan <тип> <значение>     первое сканирование (например: scan i32 100)
  next <тип> <значение>     уточнить поиск после изменения значения в игре
  aob <паттерн>             поиск по сигнатуре (48 8B ?? 90)
  read <адрес> <тип>        прочитать значение
  write <адрес> <тип> <значение>   записать значение
  freeze <адрес> <тип> <значение>  заморозить значение
  unfreeze [адрес]          снять заморозку
  status                    состояние сессии
  reset                     очистить кандидатов
  help | quit

Типы: i8 u8 i16 u16 i32 u32 i64 u64 f32 f64 string bytes
"""


def repl() -> None:
    from .memory import MemorySession

    session = MemorySession()
    print("Game Memory Agent — CLI без LLM")
    print(HELP)

    while True:
        try:
            line = input("mem> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nПока!")
            break
        if not line:
            continue

        parts = line.split()
        command = parts[0].lower()
        try:
            if command in ("help", "?", "помощь"):
                print(HELP)
            elif command in ("quit", "exit", "выход", "q"):
                break
            elif command == "attach":
                if len(parts) < 2:
                    raise ValueError("Использование: attach <имя|pid>")
                argument = parts[1]
                if argument.isdigit():
                    print(session.attach(pid=int(argument)))
                else:
                    print(session.attach(name=argument))
            elif command == "scan":
                if len(parts) < 3:
                    raise ValueError("Использование: scan <тип> <значение>")
                print(session.scan(" ".join(parts[2:]), parts[1]))
            elif command == "next":
                if len(parts) < 3:
                    raise ValueError("Использование: next <тип> <значение>")
                print(session.next_scan(" ".join(parts[2:]), parts[1]))
            elif command == "aob":
                if len(parts) < 2:
                    raise ValueError("Использование: aob <паттерн>")
                print(session.aob_scan(" ".join(parts[1:])))
            elif command == "read":
                if len(parts) < 3:
                    raise ValueError("Использование: read <адрес> <тип>")
                address = parse_address(parts[1])
                print(f"{hex(address)} = {session.read_value(address, parts[2])!r}")
            elif command == "write":
                if len(parts) < 4:
                    raise ValueError("Использование: write <адрес> <тип> <значение>")
                address = parse_address(parts[1])
                print(
                    f"{hex(address)} = "
                    f"{session.write_value(address, ' '.join(parts[3:]), parts[2])!r}"
                )
            elif command == "freeze":
                if len(parts) < 4:
                    raise ValueError("Использование: freeze <адрес> <тип> <значение>")
                print(
                    session.freeze(
                        parse_address(parts[1]), " ".join(parts[3:]), parts[2]
                    )
                )
            elif command == "unfreeze":
                address = parse_address(parts[1]) if len(parts) > 1 else None
                print(session.unfreeze(address))
            elif command == "status":
                print(session.status())
            elif command == "reset":
                print(session.reset_scan())
            else:
                print(f"Неизвестная команда: {command}. help — список команд.")
        except Exception as error:  # noqa: BLE001
            print(f"[!] {type(error).__name__}: {error}")
