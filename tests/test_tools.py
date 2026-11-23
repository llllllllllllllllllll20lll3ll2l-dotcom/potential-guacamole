"""Проверяем диспетчер инструментов на фейковой сессии — Windows не нужен."""

import pytest

from game_memory_agent.tools import SYSTEM_PROMPT, dumps, run_tool, tools


class FakeSession:
    def __init__(self):
        self.calls = []

    def attach(self, name=None, pid=None):
        self.calls.append(("attach", name, pid))
        return {"pid": pid or 4242, "name": name or "game.exe"}

    def status(self):
        return {"attached": True, "pid": 4242, "candidates": 3, "frozen": []}

    def scan(self, value, dtype):
        self.calls.append(("scan", value, dtype))
        return {"count": 1, "addresses": ["0x1000"], "truncated": False}

    def next_scan(self, value, dtype, tolerance=0.0):
        self.calls.append(("next_scan", value, dtype, tolerance))
        return {"count": 1, "addresses": ["0x1000"], "truncated": False}

    def aob_scan(self, pattern):
        self.calls.append(("aob", pattern))
        return {"count": 0, "addresses": [], "truncated": False}

    def read_value(self, address, dtype):
        return 99

    def write_value(self, address, value, dtype):
        self.calls.append(("write", address, value, dtype))
        return value

    def freeze(self, address, value, dtype):
        self.calls.append(("freeze", address, value, dtype))
        return {"frozen": hex(address), "value": value, "dtype": dtype}

    def unfreeze(self, address=None):
        self.calls.append(("unfreeze", address))
        return {"ok": True}

    def reset_scan(self):
        return {"ok": True}


def test_tools_schema_is_valid_openai_shape():
    for tool in tools():
        assert tool["type"] == "function"
        function = tool["function"]
        assert function["name"]
        assert function["description"]
        assert function["parameters"]["type"] == "object"


def test_dispatch_scan_and_next_scan():
    session = FakeSession()
    result = run_tool(session, "scan", {"dtype": "i32", "value": 100})
    assert result["count"] == 1
    assert session.calls == [("scan", 100, "i32")]

    run_tool(session, "next_scan", {"dtype": "i32", "value": 250, "tolerance": 0.5})
    assert session.calls[-1] == ("next_scan", 250, "i32", 0.5)


def test_dispatch_write_parses_hex_address():
    session = FakeSession()
    result = run_tool(
        session, "write", {"address": "0x7ff6abcd", "dtype": "i32", "value": 999999}
    )
    assert result["address"] == "0x7ff6abcd"
    assert session.calls == [("write", 0x7FF6ABCD, 999999, "i32")]


def test_dispatch_read():
    session = FakeSession()
    result = run_tool(session, "read", {"address": 0x1000, "dtype": "f32"})
    assert result == {"address": "0x1000", "value": 99, "dtype": "f32"}


def test_dispatch_freeze_unfreeze():
    session = FakeSession()
    run_tool(session, "freeze", {"address": 0x1000, "dtype": "i32", "value": 100})
    assert session.calls[-1] == ("freeze", 0x1000, 100, "i32")
    run_tool(session, "unfreeze", {})
    assert session.calls[-1] == ("unfreeze", None)


def test_unknown_tool_raises():
    with pytest.raises(ValueError):
        run_tool(FakeSession(), "nope", {})


def test_system_prompt_safety_rules():
    assert "античит" in SYSTEM_PROMPT
    assert "офлайн" in SYSTEM_PROMPT


def test_dumps_keeps_cyrillic():
    assert dumps({"msg": "Привет"}) == '{"msg": "Привет"}'
