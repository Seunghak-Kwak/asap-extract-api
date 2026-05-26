import io
from datetime import datetime
from decimal import Decimal

from app.extract.writer import CsvBatchWriter, _stringify


def test_header_then_rows() -> None:
    buf = io.BytesIO()
    w = CsvBatchWriter(buf, ["id", "occurred_at", "payload"])
    w.write_batch(
        [
            {"id": 1, "occurred_at": datetime(2026, 1, 1, 12), "payload": {"a": 1}},
            {"id": 2, "occurred_at": datetime(2026, 1, 1, 13), "payload": [1, 2]},
        ]
    )
    text = buf.getvalue().decode("utf-8")
    lines = text.strip().split("\r\n")
    assert lines[0] == "id,occurred_at,payload"
    assert lines[1].startswith("1,2026-01-01T12:00:00,")
    # JSON survives CSV
    assert '"{""a"":1}"' in lines[1]


def test_none_becomes_empty_field() -> None:
    buf = io.BytesIO()
    w = CsvBatchWriter(buf, ["a", "b"])
    w.write_batch([{"a": None, "b": "x"}])
    line = buf.getvalue().decode("utf-8").strip().split("\r\n")[1]
    assert line == ",x"


def test_decimal_zero_renders_as_zero_not_scientific() -> None:
    # str(Decimal('0E-20')) == '0E-20' — that broke real-world CSVs.
    assert _stringify(Decimal("0E-20")) == "0"
    assert _stringify(Decimal("0")) == "0"
    assert _stringify(Decimal("0.0000")) == "0"


def test_decimal_keeps_significant_digits_without_scientific() -> None:
    assert _stringify(Decimal("1.5")) == "1.5"
    assert _stringify(Decimal("1.50")) == "1.5"
    assert _stringify(Decimal("100")) == "100"
    assert _stringify(Decimal("-0.25")) == "-0.25"
    assert _stringify(Decimal("1E10")) == "10000000000"
