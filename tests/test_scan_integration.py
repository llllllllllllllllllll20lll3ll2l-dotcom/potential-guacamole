"""Интеграционные тесты алгоритма сканирования на симулированной памяти.

MemorySession умеет работать с любой реализацией regions/read/write,
поэтому весь код сканирования можно проверить без Windows.
"""

from __future__ import annotations

import time

from game_memory_agent.core import pack_value
from game_memory_agent.memory import MemorySession


class FakeSession(MemorySession):
    """Сессия, у которой вместо WinAPI — словарь regions с байтами."""

    CHUNK_SIZE = 32  # маленькие чанки, чтобы проверить границы

    def __init__(self, regions: dict[int, bytes], unreadable: set[int] | None = None):
        super().__init__()
        self._regions = {base: bytes(data) for base, data in regions.items()}
        self._unreadable = unreadable or set()
        self.writes: list[tuple[int, bytes]] = []

    def _require(self) -> None:
        if not self._regions:
            raise RuntimeError("нет памяти")

    def regions(self):
        for base, data in sorted(self._regions.items()):
            yield base, len(data)

    def _locate(self, address: int):
        for base, data in sorted(self._regions.items()):
            if base <= address < base + len(data):
                return base, data
        return None

    def read(self, address: int, size: int) -> bytes:
        if address in self._unreadable:
            raise MemoryError(f"unreadable {hex(address)}")
        located = self._locate(address)
        if located is None:
            raise MemoryError(f"unreadable {hex(address)}")
        base, data = located
        offset = address - base
        return data[offset : offset + size]

    def write(self, address: int, data: bytes) -> None:
        located = self._locate(address)
        if located is None:
            raise MemoryError(f"unreadable {hex(address)}")
        base, blob = located
        offset = address - base
        mutable = bytearray(blob)
        mutable[offset : offset + len(data)] = data
        self._regions[base] = bytes(mutable)
        self.writes.append((address, bytes(data)))


def region_with(offsets: dict[int, bytes], size: int = 0x200) -> bytes:
    data = bytearray(size)
    for offset, payload in offsets.items():
        data[offset : offset + len(payload)] = payload
    return bytes(data)


def test_scan_finds_values_on_chunk_boundaries():
    data = region_with(
        {
            30: pack_value(1337, "i32"),  # пересекает границу чанка 32
            62: pack_value(1337, "i32"),  # пересекает границу 64
            96: pack_value(1337, "i32"),  # ровно на границе 96
            150: pack_value(1337, "i32"),
        }
    )
    session = FakeSession({0x1000: data})
    result = session.scan(1337, "i32")

    assert result["count"] == 4
    assert result["truncated"] is False
    assert [int(a, 16) for a in result["addresses"]] == [
        0x1000 + 30,
        0x1000 + 62,
        0x1000 + 96,
        0x1000 + 150,
    ]


def test_scan_multiple_regions():
    left = region_with({10: pack_value(7, "i32")})
    right = region_with({200: pack_value(7, "i32")})
    session = FakeSession({0x1000: left, 0x9000: right})
    result = session.scan(7, "i32")
    assert result["count"] == 2
    assert [int(a, 16) for a in result["addresses"]] == [0x100A, 0x90C8]


def test_scan_truncates_at_max_results():
    data = b"".join(pack_value(5, "i32") for _ in range(10))
    session = FakeSession({0x1000: data})
    result = session.scan(5, "i32", max_results=3)
    assert result["count"] == 3
    assert result["truncated"] is True


def test_next_scan_keeps_only_changed_addresses():
    data = region_with(
        {
            16: pack_value(100, "i32"),
            64: pack_value(100, "i32"),
            128: pack_value(100, "i32"),
        }
    )
    session = FakeSession({0x1000: data})
    assert session.scan(100, "i32")["count"] == 3

    session.write(0x1000 + 16, pack_value(250, "i32"))
    session.write(0x1000 + 64, pack_value(300, "i32"))
    session.write(0x1000 + 128, pack_value(250, "i32"))

    result = session.next_scan(250, "i32")
    assert result["count"] == 2
    assert [int(a, 16) for a in result["addresses"]] == [0x1010, 0x1080]


def test_next_scan_float_tolerance():
    data = region_with({40: pack_value(10.0, "f32")})
    session = FakeSession({0x1000: data})
    assert session.scan(10.0, "f32")["count"] == 1

    # игра слегка изменила значение — байты уже другие
    session.write(0x1000 + 40, pack_value(10.000001, "f32"))

    exact = session.next_scan(10.0, "f32")
    assert exact["count"] == 0

    session.candidates = [0x1000 + 40]
    tolerant = session.next_scan(10.0, "f32", tolerance=0.001)
    assert tolerant["count"] == 1


def test_aob_scan_with_wildcards_across_boundary():
    data = region_with(
        {
            30: bytes.fromhex("488B1590"),
            100: bytes.fromhex("488BAA90"),
            104: bytes.fromhex("488CBB90"),
        }
    )
    session = FakeSession({0x1000: data})
    result = session.aob_scan("48 8B ?? 90")
    assert result["count"] == 2
    assert [int(a, 16) for a in result["addresses"]] == [0x1000 + 30, 0x1000 + 100]


def test_scan_skips_unreadable_chunks():
    data = region_with({20: pack_value(42, "i32"), 160: pack_value(42, "i32")})
    # первый чанк (0..32) недоступен для чтения
    session = FakeSession({0x1000: data}, unreadable={0x1000})
    result = session.scan(42, "i32")
    assert result["count"] == 1
    assert [int(a, 16) for a in result["addresses"]] == [0x1000 + 160]


def test_read_value_string_stops_at_nul():
    session = FakeSession({0x1000: b"hello\x00world\x00"})
    assert session.read_value(0x1000, "string") == "hello"


def test_read_value_bytes_returns_hex():
    session = FakeSession({0x1000: bytes.fromhex("488B0090")})
    assert session.read_value(0x1000, "bytes", length=4) == "48 8b 00 90"


def test_freeze_writes_value_periodically():
    session = FakeSession({0x1000: region_with({40: pack_value(1, "i32")})})
    try:
        session.freeze(0x1000 + 40, 999999, "i32")
        time.sleep(0.35)
        assert session.writes, "заморозка должна периодически писать значение"
        assert all(data == pack_value(999999, "i32") for _addr, data in session.writes)
        assert session.status()["frozen"] == ["0x1028"]

        session.unfreeze()
        time.sleep(0.05)
        count_after_unfreeze = len(session.writes)
        time.sleep(0.3)
        assert len(session.writes) == count_after_unfreeze, (
            "после unfreeze записей быть не должно"
        )
    finally:
        session.detach()


def test_find_processes_by_name_forms():
    from game_memory_agent.memory import find_processes

    processes = [
        {"pid": 1, "name": "Game.exe"},
        {"pid": 2, "name": "Game2.exe"},
        {"pid": 3, "name": "notepad.exe"},
    ]

    assert [p["pid"] for p in find_processes(processes, "game.exe")] == [1]
    assert [p["pid"] for p in find_processes(processes, "game")] == [1]
    assert [p["pid"] for p in find_processes(processes, r"C:\Games\Game.exe")] == [1]
    assert [p["pid"] for p in find_processes(processes, "GAME")] == [1]
    assert [p["pid"] for p in find_processes(processes, "nothing")] == []
