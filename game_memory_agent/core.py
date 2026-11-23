"""Чистая логика, не зависящая от Windows: кодирование значений, AOB-паттерны, поиск в буфере.

Этот модуль тестируется на любой ОС — вся работа с памятью процесса в memory.py.
"""
from __future__ import annotations

import re
import struct
from typing import Any

# dtype -> (struct format без порядка байт, размер в байтах)
DTYPES: dict[str, tuple[str, int]] = {
    "i8": ("b", 1),
    "u8": ("B", 1),
    "i16": ("h", 2),
    "u16": ("H", 2),
    "i32": ("i", 4),
    "u32": ("I", 4),
    "i64": ("q", 8),
    "u64": ("Q", 8),
    "f32": ("f", 4),
    "f64": ("d", 8),
}

# Человеческие алиасы, которые может использовать LLM или пользователь
DTYPE_ALIASES = {
    "int": "i32",
    "int8": "i8",
    "int16": "i16",
    "int32": "i32",
    "int64": "i64",
    "long": "i64",
    "uint": "u32",
    "uint8": "u8",
    "uint16": "u16",
    "uint32": "u32",
    "uint64": "u64",
    "byte": "i8",
    "short": "i16",
    "float": "f32",
    "float32": "f32",
    "double": "f64",
    "float64": "f64",
    "str": "string",
    "text": "string",
}

SCAN_DTYPES = list(DTYPES) + ["string", "bytes"]


def normalize_dtype(dtype: str) -> str:
    """Приводит 'int', 'INT32', 'Float' и т.п. к каноническому имени."""
    key = str(dtype).strip().lower()
    key = DTYPE_ALIASES.get(key, key)
    if key in DTYPES or key in ("string", "bytes", "aob"):
        return key
    raise ValueError(
        f"Неизвестный тип данных {dtype!r}. Доступно: {', '.join(SCAN_DTYPES)}, aob"
    )


def pack_value(value: Any, dtype: str) -> bytes:
    """Кодирует значение в байты так, как оно лежит в памяти (little-endian)."""
    dtype = normalize_dtype(dtype)
    if dtype in DTYPES:
        fmt, _ = DTYPES[dtype]
        number = float(value) if fmt in "fd" else int(value)
        return struct.pack("<" + fmt, number)
    if dtype == "string":
        return str(value).encode("utf-8")
    if dtype == "bytes":
        cleaned = str(value).replace(" ", "").replace("0x", "").replace("0X", "")
        return bytes.fromhex(cleaned)
    raise ValueError("Тип 'aob' нельзя использовать для записи значения")


def unpack_value(raw: bytes, dtype: str) -> Any:
    """Декодирует байты из памяти в число/строку."""
    dtype = normalize_dtype(dtype)
    if dtype in DTYPES:
        fmt, size = DTYPES[dtype]
        if len(raw) < size:
            raise ValueError(f"Нужно {size} байт, получено {len(raw)}")
        return struct.unpack("<" + fmt, raw[:size])[0]
    if dtype == "string":
        return raw.decode("utf-8", errors="replace")
    if dtype == "bytes":
        return raw.hex(" ")
    raise ValueError("Тип 'aob' нельзя использовать для чтения значения")


def value_size(dtype: str, value: Any = None) -> int:
    """Размер значения в байтах (нужен для поиска)."""
    dtype = normalize_dtype(dtype)
    if dtype in DTYPES:
        return DTYPES[dtype][1]
    if dtype == "string":
        return len(str(value).encode("utf-8"))
    if dtype == "bytes":
        return len(pack_value(value, dtype))
    raise ValueError("Тип 'aob' не имеет фиксированного размера")


def _tokenize_pattern(pattern: str) -> list[str]:
    """Разбивает '48 8B ?? 90' или '488B??90' на токены по одному байту."""
    cleaned = str(pattern).strip().replace(",", " ").replace("0x", "").replace("0X", "")
    if not cleaned:
        raise ValueError("Пустой AOB-паттерн")
    if " " in cleaned:
        tokens = cleaned.split()
    else:
        if len(cleaned) % 2:
            raise ValueError(f"Нечётная длина hex-паттерна: {pattern!r}")
        tokens = [cleaned[i : i + 2] for i in range(0, len(cleaned), 2)]
    for token in tokens:
        if token not in ("?", "??") and len(token) != 2:
            raise ValueError(f"Непонятный токен {token!r} в паттерне {pattern!r}")
    return tokens


def parse_pattern(pattern: str) -> re.Pattern[bytes]:
    """Компилирует AOB-паттерн ('48 8B ?? 90') в bytes-regex."""
    parts = []
    for token in _tokenize_pattern(pattern):
        if token in ("?", "??"):
            parts.append(b".")
        else:
            parts.append(re.escape(bytes([int(token, 16)])))
    return re.compile(b"".join(parts), re.DOTALL)


def pattern_length(pattern: str) -> int:
    """Длина паттерна в байтах."""
    return len(_tokenize_pattern(pattern))


def find_all(buffer: bytes, needle: bytes, base: int = 0) -> list[int]:
    """Все смещения needle в buffer (в абсолютных адресах, если передан base)."""
    out: list[int] = []
    if not needle or not buffer:
        return out
    start = 0
    while True:
        index = buffer.find(needle, start)
        if index == -1:
            return out
        out.append(base + index)
        start = index + 1


def find_pattern_all(buffer: bytes, regex: re.Pattern[bytes], base: int = 0) -> list[int]:
    """Все смещения, соответствующие AOB-паттерну."""
    return [base + match.start() for match in regex.finditer(buffer)]


def parse_address(value: Any) -> int:
    """Принимает int, '0x7ff6...', '7ff6...' (hex) или '140701...'(dec)."""
    if isinstance(value, bool):
        raise ValueError("Адрес должен быть числом или строкой")
    if isinstance(value, int):
        return value
    text = str(value).strip().lower().replace("`", "")
    if text.startswith("0x"):
        return int(text, 16)
    # Без префикса: если есть буквы a-f — это hex (стиль Cheat Engine),
    # иначе считаем десятичным числом.
    if any(char in "abcdef" for char in text):
        return int(text, 16)
    return int(text, 10)
