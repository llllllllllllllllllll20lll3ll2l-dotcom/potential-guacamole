"""Доступ к памяти процесса Windows через ctypes, без сторонних зависимостей.

Модуль импортируется только на Windows (на других ОС падает с понятной ошибкой).
Все вызовы WinAPI идут с явными argtypes/restype — это критично на x64.
"""
from __future__ import annotations

import ctypes
import sys
import threading
import time
from ctypes import wintypes
from typing import Any, Iterator

if sys.platform != "win32":
    raise OSError(
        "Доступ к памяти процесса работает только на Windows. "
        "На Linux используйте scanmem/GameConqueror."
    )

from .core import (
    find_all,
    find_pattern_all,
    normalize_dtype,
    pack_value,
    parse_pattern,
    pattern_length,
    unpack_value,
    value_size,
)

k32 = ctypes.WinDLL("kernel32", use_last_error=True)

# --- Константы WinAPI -------------------------------------------------------
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
PROCESS_ALL = (
    PROCESS_QUERY_INFORMATION | PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION
)

MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100
# readable: PAGE_READONLY, PAGE_READWRITE, PAGE_EXECUTE_READ,
#           PAGE_EXECUTE_READWRITE, PAGE_EXECUTE_WRITECOPY
READABLE_PROTECTIONS = {0x02, 0x04, 0x20, 0x40, 0x80}

TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

# Верх пользовательского адресного пространства x64 (выше — пространство ядра).
MAX_ADDRESS = 0x7FFFFFFEFFFF


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


# --- Прототипы функций (обязательно: иначе ctypes обрежет 64-битные указатели) ---
k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
k32.OpenProcess.restype = wintypes.HANDLE

k32.CloseHandle.argtypes = [wintypes.HANDLE]
k32.CloseHandle.restype = wintypes.BOOL

k32.VirtualQueryEx.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.POINTER(MEMORY_BASIC_INFORMATION),
    ctypes.c_size_t,
]
k32.VirtualQueryEx.restype = ctypes.c_size_t

k32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
k32.ReadProcessMemory.restype = wintypes.BOOL

k32.WriteProcessMemory.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
k32.WriteProcessMemory.restype = wintypes.BOOL

k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE

k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
k32.Process32FirstW.restype = wintypes.BOOL
k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
k32.Process32NextW.restype = wintypes.BOOL


def list_processes() -> list[dict[str, Any]]:
    """Список процессов через CreateToolhelp32Snapshot."""
    snapshot = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE or not snapshot:
        raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot")
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        processes: list[dict[str, Any]] = []
        ok = k32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            processes.append({"pid": int(entry.th32ProcessID), "name": entry.szExeFile})
            ok = k32.Process32NextW(snapshot, ctypes.byref(entry))
        return processes
    finally:
        k32.CloseHandle(snapshot)


class MemorySession:
    """Подключение к процессу + сканирование + чтение/запись + заморозка значений."""

    CHUNK_SIZE = 4 * 1024 * 1024  # 4 MiB на чтение

    def __init__(self) -> None:
        self.handle: int | None = None
        self.pid: int | None = None
        self.name: str | None = None
        self.candidates: list[int] = []
        self._freezer: Freezer | None = None

    # --- подключение -------------------------------------------------------
    def _require(self) -> None:
        if not self.handle:
            raise RuntimeError("Сначала подключись к процессу: attach(name=... | pid=...)")

    def attach(self, name: str | None = None, pid: int | None = None) -> dict[str, Any]:
        processes = list_processes()
        if pid is None and name is not None:
            wanted = name.strip().lower()
            if not wanted.endswith(".exe"):
                wanted += ".exe"
            matches = [p for p in processes if p["name"].lower() == wanted]
            if not matches:
                # пробуем частичное совпадение по имени без .exe
                stem = wanted[:-4]
                matches = [
                    p
                    for p in processes
                    if p["name"].lower().startswith(stem) and p["name"].lower().endswith(".exe")
                ]
            if not matches:
                raise ValueError(
                    f"Процесс {name!r} не найден. Запусти игру или укажи pid."
                )
            if len(matches) > 1:
                listing = ", ".join(f"{p['name']} (pid {p['pid']})" for p in matches)
                raise ValueError(f"Найдено несколько процессов: {listing}. Укажи pid.")
            pid = int(matches[0]["pid"])
        if pid is None:
            raise ValueError("Нужно указать имя процесса (name) или pid")

        handle = k32.OpenProcess(PROCESS_ALL, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            raise OSError(
                error,
                f"OpenProcess(pid={pid}) не удался (код {error}). "
                "Запусти терминал от имени администратора.",
            )

        self.detach()
        self.handle = handle
        self.pid = pid
        self.name = next((p["name"] for p in processes if p["pid"] == pid), "?")
        self.candidates = []
        return {"pid": pid, "name": self.name}

    def detach(self) -> None:
        if self._freezer is not None:
            self._freezer.stop()
            self._freezer = None
        if self.handle:
            k32.CloseHandle(self.handle)
        self.handle = None
        self.pid = None
        self.name = None
        self.candidates = []

    # --- работа с памятью --------------------------------------------------
    def regions(self) -> Iterator[tuple[int, int]]:
        """Коммитнутые регионы памяти (base, size), доступные для чтения."""
        self._require()
        address = 0
        mbi = MEMORY_BASIC_INFORMATION()
        while address < MAX_ADDRESS:
            result = k32.VirtualQueryEx(
                self.handle, ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi)
            )
            if result == 0:
                break
            base = int(mbi.BaseAddress or 0)
            size = int(mbi.RegionSize)
            if (
                mbi.State == MEM_COMMIT
                and (mbi.Protect & 0xFF) in READABLE_PROTECTIONS
                and not (mbi.Protect & PAGE_GUARD)
                and size > 0
            ):
                yield base, size
            next_address = base + size
            if next_address <= address:
                break
            address = next_address

    def read(self, address: int, size: int) -> bytes:
        self._require()
        if size <= 0:
            return b""
        buffer = ctypes.create_string_buffer(size)
        bytes_read = ctypes.c_size_t(0)
        ok = k32.ReadProcessMemory(
            self.handle,
            ctypes.c_void_p(address),
            ctypes.cast(buffer, ctypes.c_void_p),
            size,
            ctypes.byref(bytes_read),
        )
        if not ok:
            error = ctypes.get_last_error()
            raise MemoryError(f"ReadProcessMemory({hex(address)}, {size}) ошибка {error}")
        return buffer.raw[: bytes_read.value]

    def write(self, address: int, data: bytes) -> None:
        self._require()
        if not data:
            return
        buffer = ctypes.create_string_buffer(data, len(data))
        written = ctypes.c_size_t(0)
        ok = k32.WriteProcessMemory(
            self.handle,
            ctypes.c_void_p(address),
            ctypes.cast(buffer, ctypes.c_void_p),
            len(data),
            ctypes.byref(written),
        )
        if not ok or written.value != len(data):
            error = ctypes.get_last_error()
            raise MemoryError(f"WriteProcessMemory({hex(address)}) ошибка {error}")

    def read_value(self, address: int, dtype: str, length: int = 64) -> Any:
        """Читает значение. Для string/bytes читает до `length` байт (string — до NUL)."""
        dtype = normalize_dtype(dtype)
        if dtype == "string":
            raw = self.read(address, length).split(b"\x00", 1)[0]
            return unpack_value(raw, "string")
        if dtype == "bytes":
            return unpack_value(self.read(address, length), "bytes")
        return unpack_value(self.read(address, value_size(dtype)), dtype)

    def write_value(self, address: int, value: Any, dtype: str) -> Any:
        dtype = normalize_dtype(dtype)
        self.write(address, pack_value(value, dtype))
        return self.read_value(address, dtype)

    # --- сканирование ------------------------------------------------------
    def _result(self, addresses: list[int], truncated: bool) -> dict[str, Any]:
        return {
            "count": len(addresses),
            "addresses": [hex(a) for a in addresses[:40]],
            "truncated": truncated,
        }

    def scan(self, value: Any, dtype: str, max_results: int = 200_000) -> dict[str, Any]:
        """Первое сканирование по всему адресному пространству."""
        dtype = normalize_dtype(dtype)
        if dtype == "aob":
            return self.aob_scan(str(value), max_results=max_results)
        needle = pack_value(value, dtype)
        size = len(needle)
        found: list[int] = []
        truncated = False

        for base, region_size in self.regions():
            if len(found) >= max_results:
                truncated = True
                break
            offset = 0
            overlap = max(size - 1, 0)
            while offset < region_size:
                chunk = min(self.CHUNK_SIZE, region_size - offset)
                try:
                    data = self.read(base + offset, min(chunk + overlap, region_size - offset))
                except MemoryError:
                    offset += chunk
                    continue
                for local in find_all(data, needle, 0):
                    if local >= chunk:
                        continue  # совпадение в перекрытии найдётся в следующем чанке
                    found.append(base + offset + local)
                    if len(found) >= max_results:
                        truncated = True
                        break
                if truncated:
                    break
                offset += chunk

        self.candidates = found
        result = self._result(found, truncated)
        result["dtype"] = dtype
        return result

    def next_scan(
        self, value: Any, dtype: str, tolerance: float = 0.0, max_results: int = 200_000
    ) -> dict[str, Any]:
        """Уточняющее сканирование: проверяет только адреса из прошлого поиска."""
        dtype = normalize_dtype(dtype)
        if dtype == "aob":
            return self.aob_scan(str(value), max_results=max_results)
        needle = pack_value(value, dtype)
        size = len(needle)
        kept: list[int] = []
        target = float(value) if dtype in ("f32", "f64") else None

        for address in self.candidates:
            try:
                raw = self.read(address, size)
            except MemoryError:
                continue
            if target is not None and tolerance > 0:
                try:
                    current = float(unpack_value(raw, dtype))
                except ValueError:
                    continue
                if abs(current - target) <= tolerance:
                    kept.append(address)
            elif raw == needle:
                kept.append(address)

        self.candidates = kept
        result = self._result(kept, len(kept) > max_results)
        result["dtype"] = dtype
        return result

    def aob_scan(self, pattern: str, max_results: int = 50_000) -> dict[str, Any]:
        """Поиск по байтовому паттерну с wildcard: '48 8B ?? 90'."""
        regex = parse_pattern(pattern)
        size = pattern_length(pattern)
        found: list[int] = []
        truncated = False

        for base, region_size in self.regions():
            if len(found) >= max_results:
                truncated = True
                break
            offset = 0
            overlap = max(size - 1, 0)
            while offset < region_size:
                chunk = min(self.CHUNK_SIZE, region_size - offset)
                try:
                    data = self.read(base + offset, min(chunk + overlap, region_size - offset))
                except MemoryError:
                    offset += chunk
                    continue
                for local in find_pattern_all(data, regex, 0):
                    if local >= chunk:
                        continue
                    found.append(base + offset + local)
                    if len(found) >= max_results:
                        truncated = True
                        break
                if truncated:
                    break
                offset += chunk

        self.candidates = found
        result = self._result(found, truncated)
        result["pattern"] = pattern
        return result

    def reset_scan(self) -> dict[str, Any]:
        self.candidates = []
        return {"ok": True}

    # --- заморозка значения ------------------------------------------------
    def freeze(self, address: int, value: Any, dtype: str) -> dict[str, Any]:
        self._require()
        if self._freezer is None:
            self._freezer = Freezer(self)
        self._freezer.set(address, value, dtype)
        return {"frozen": hex(address), "value": value, "dtype": normalize_dtype(dtype)}

    def unfreeze(self, address: int | None = None) -> dict[str, Any]:
        if self._freezer is not None:
            self._freezer.clear(address)
        return {"ok": True}

    def status(self) -> dict[str, Any]:
        frozen = []
        if self._freezer is not None:
            frozen = [hex(a) for a in self._freezer.addresses()]
        return {
            "attached": bool(self.handle),
            "pid": self.pid,
            "name": self.name,
            "candidates": len(self.candidates),
            "frozen": frozen,
        }


class Freezer:
    """Фоновый поток, который каждые `interval` секунд перезаписывает значение."""

    def __init__(self, session: MemorySession, interval: float = 0.1) -> None:
        self.session = session
        self.interval = interval
        self._entries: dict[int, tuple[bytes, str]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def set(self, address: int, value: Any, dtype: str) -> None:
        data = pack_value(value, normalize_dtype(dtype))
        with self._lock:
            self._entries[address] = (data, normalize_dtype(dtype))
        self._ensure_thread()

    def clear(self, address: int | None = None) -> None:
        with self._lock:
            if address is None:
                self._entries.clear()
            else:
                self._entries.pop(address, None)

    def addresses(self) -> list[int]:
        with self._lock:
            return list(self._entries)

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="memory-freezer", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                items = list(self._entries.items())
            if not items:
                time.sleep(self.interval)
                continue
            for address, (data, _dtype) in items:
                try:
                    self.session.write(address, data)
                except MemoryError:
                    pass
            time.sleep(self.interval)

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1.0)
        self._thread = None
