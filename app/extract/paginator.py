"""Pagination over the source DB.

Two strategies (chosen by ds.sort_unique):

1) keyset (default) — `WHERE (sort) > (cursor) LIMIT N`. O(N) per batch,
   scales to arbitrary row counts. Requires the sort tuple to be unique
   per row; otherwise rows at batch boundaries are dropped.

2) offset (sort_unique=False) — `LIMIT N OFFSET M`. Each batch re-scans
   the M skipped rows; cost grows with the offset. Only for tables that
   genuinely have no unique sort column. Caveats: a row inserted/deleted
   mid-extract may shift offsets and cause duplicate or skipped rows.

The query builder accepts only column names from the Dataset definition —
never arbitrary user input — so SQL injection is impossible by construction.
"""

from collections.abc import AsyncIterator
from typing import Any

import aiomysql

from app.extract.registry import Dataset


def _filter_clause(ds: Dataset, filters: dict[str, Any]) -> tuple[str, list[Any]]:
    where: list[str] = []
    params: list[Any] = []

    if "from" in filters:
        where.append(f"`{ds.time_column}` >= %s")
        params.append(filters["from"])
    if "to" in filters:
        where.append(f"`{ds.time_column}` < %s")
        params.append(filters["to"])

    for k in ds.optional_filters:
        if k in ("from", "to") or k not in filters:
            continue
        if k in ds.list_filters:
            ph = ", ".join(["%s"] * len(filters[k]))
            where.append(f"`{k}` IN ({ph})")
            params.extend(filters[k])
        else:
            where.append(f"`{k}` = %s")
            params.append(filters[k])

    return (" AND ".join(where) if where else "1=1"), params


def _build_keyset_query(
    ds: Dataset,
    filters: dict[str, Any],
    cursor: tuple[Any, ...] | None,
    batch_size: int,
) -> tuple[str, list[Any]]:
    cols = ", ".join(f"`{c}`" for c in ds.columns)
    sort = ", ".join(f"`{c}`" for c in ds.sort_columns)
    where, params = _filter_clause(ds, filters)
    if cursor is not None:
        ph = ", ".join(["%s"] * len(ds.sort_columns))
        pred = f"({sort}) > ({ph})"
        where = pred if where == "1=1" else f"{where} AND {pred}"
        params = [*params, *cursor]
    sql = (
        f"SELECT {cols} FROM `{ds.table}` "
        f"WHERE {where} "
        f"ORDER BY {sort} "
        f"LIMIT {int(batch_size)}"
    )
    return sql, params


def _build_offset_query(
    ds: Dataset,
    filters: dict[str, Any],
    offset: int,
    batch_size: int,
) -> tuple[str, list[Any]]:
    cols = ", ".join(f"`{c}`" for c in ds.columns)
    sort = ", ".join(f"`{c}`" for c in ds.sort_columns)
    where, params = _filter_clause(ds, filters)
    sql = (
        f"SELECT {cols} FROM `{ds.table}` "
        f"WHERE {where} "
        f"ORDER BY {sort} "
        f"LIMIT {int(batch_size)} OFFSET {int(offset)}"
    )
    return sql, params


async def _iter_keyset(
    conn: aiomysql.Connection, ds: Dataset, filters: dict[str, Any], batch_size: int
) -> AsyncIterator[list[dict[str, Any]]]:
    cursor: tuple[Any, ...] | None = None
    while True:
        sql, params = _build_keyset_query(ds, filters, cursor, batch_size)
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()
        if not rows:
            return
        yield rows
        last = rows[-1]
        cursor = tuple(last[c] for c in ds.sort_columns)
        if len(rows) < batch_size:
            return


async def _iter_offset(
    conn: aiomysql.Connection, ds: Dataset, filters: dict[str, Any], batch_size: int
) -> AsyncIterator[list[dict[str, Any]]]:
    offset = 0
    while True:
        sql, params = _build_offset_query(ds, filters, offset, batch_size)
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()
        if not rows:
            return
        yield rows
        offset += len(rows)
        if len(rows) < batch_size:
            return


async def iter_batches(
    conn: aiomysql.Connection,
    ds: Dataset,
    filters: dict[str, Any],
    batch_size: int,
) -> AsyncIterator[list[dict[str, Any]]]:
    if ds.sort_unique:
        async for batch in _iter_keyset(conn, ds, filters, batch_size):
            yield batch
    else:
        async for batch in _iter_offset(conn, ds, filters, batch_size):
            yield batch
