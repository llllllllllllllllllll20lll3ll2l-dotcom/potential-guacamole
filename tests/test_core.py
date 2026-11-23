from game_memory_agent.core import (
    DTYPES,
    find_all,
    find_pattern_all,
    normalize_dtype,
    pack_value,
    parse_address,
    parse_pattern,
    pattern_length,
    unpack_value,
    value_size,
)


def test_pack_unpack_roundtrip_all_numeric_types():
    for dtype, (fmt, _size) in DTYPES.items():
        if fmt in "fd":
            value = 1.5
        elif fmt in "bB":
            value = 12
        elif fmt in "hH":
            value = 12345
        else:
            value = 1234567
        raw = pack_value(value, dtype)
        assert len(raw) == value_size(dtype)
        assert unpack_value(raw, dtype) == value


def test_dtype_aliases():
    assert normalize_dtype("int") == "i32"
    assert normalize_dtype("FLOAT") == "f32"
    assert normalize_dtype("Double") == "f64"
    assert normalize_dtype("u32") == "u32"


def test_string_and_bytes_roundtrip():
    assert pack_value("abc", "string") == b"abc"
    assert unpack_value(b"abc", "string") == "abc"
    assert pack_value("48 8B 00", "bytes") == bytes.fromhex("488B00")


def test_pattern_with_wildcards():
    regex = parse_pattern("48 8B ?? 90")
    assert regex.search(bytes.fromhex("488B1590"))
    assert not regex.search(bytes.fromhex("488C1590"))
    assert pattern_length("48 8B ?? 90") == 4
    assert pattern_length("488B??90") == 4


def test_pattern_compact_form():
    regex = parse_pattern("488B??90")
    assert regex.search(bytes.fromhex("AAAA488BAB90"))


def test_find_all_returns_absolute_addresses():
    buffer = b"\x01\x02\x03\x01\x02\x03"
    assert find_all(buffer, b"\x01\x02", 0x1000) == [0x1000, 0x1003]
    assert find_all(buffer, b"\x99") == []


def test_find_pattern_all():
    regex = parse_pattern("90 ?? CC")
    buffer = bytes.fromhex("90AACC")
    assert find_pattern_all(buffer, regex, 0x10) == [0x10]


def test_parse_address_forms():
    assert parse_address(123) == 123
    assert parse_address("0x7ff6abcd") == 0x7FF6ABCD
    assert parse_address("7ff6abcd") == 0x7FF6ABCD
    assert parse_address("140701") == 140701


def test_parse_address_rejects_garbage():
    import pytest

    with pytest.raises(ValueError):
        parse_address("не адрес")
