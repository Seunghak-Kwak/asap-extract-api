"""CSV writer that serialises one batch at a time.

Operates on a binary file handle so the worker can `fsync` it. Each row is
emitted via the stdlib `csv` module against an in-memory buffer (StringIO)
*per batch*, then written out as bytes. Memory holds at most one batch.

JSON-typed columns (we know about them from the Dataset's schema indirectly —
here we just stringify dicts/lists) are encoded as compact JSON strings so they
survive a round-trip through CSV.
"""

import csv
import io
import json
from datetime import date, datetime
from typing import Any, BinaryIO


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
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
