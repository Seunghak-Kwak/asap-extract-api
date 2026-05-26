"""CSV batch writer.

JSON dict/list values are encoded as compact JSON strings so they survive
a round-trip through CSV.
"""

import csv
import io
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, BinaryIO


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        # Avoid scientific notation (str(Decimal('0E-20')) == '0E-20').
        # Render fixed-point, then strip trailing zeros/dot for clean output.
        s = format(value, "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s or "0"
    return str(value)


class CsvBatchWriter:
    def __init__(self, fh: BinaryIO, columns: list[str]) -> None:
        self._fh = fh
        self._columns = columns
        self._wrote_header = False

    def write_header(self) -> int:
        buf = io.StringIO(newline="")
        csv.writer(buf).writerow(self._columns)
        data = buf.getvalue().encode("utf-8")
        self._fh.write(data)
        self._wrote_header = True
        return len(data)

    def write_batch(self, rows: list[dict[str, Any]]) -> int:
        if not self._wrote_header:
            written = self.write_header()
        else:
            written = 0
        buf = io.StringIO(newline="")
        w = csv.writer(buf)
        for row in rows:
            w.writerow(_stringify(row.get(c)) for c in self._columns)
        data = buf.getvalue().encode("utf-8")
        self._fh.write(data)
        return written + len(data)
