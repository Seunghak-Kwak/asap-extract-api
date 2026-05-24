"""Keyset pagination over the source DB.

Builds a parameterised query from a Dataset + validated filters + a last-seen
cursor. The cursor is whatever the previous batch's last row's sort columns
were. No LIMIT/OFFSET.

Why row-value comparison `(a, b) > (x, y)`:
    It expresses "the next row after (x, y) under ORDER BY a, b" in one
    predicate, and MySQL/SingleStore can use the composite index for it.

The query builder accepts only column names from the Dataset definition — never
arbitrary user input — so SQL injection is impossible by construction.
"""

from collections.abc import AsyncIterator
from typing import Any

import aiomysql

from app.extract.registry import Dataset


def _build_query(
    ds: Dataset,
    filters: dict[str, Any],
    cursor: tuple[Any, ...] | None,
    batch_size: int,
) -> tuple[str, list[Any]]:
    cols = ", ".join(f"`{c}`" for c in ds.columns)
    sort = ", ".join(f"`{c}`" for c in ds.sort_columns)
    where: list[str] = []
    params: list[Any] = []

    if "from" in filters:
        where.append(f"`{ds.time_column}` >= %s")
        params.append(filters["from"])
    if "to" in filters:
        where.append(f"`{ds.time_column}` < %s")
        params.append(filters["to"])

    for k in ds.optional_filters:
        if k not in filters:
            continue
        if k in ds.list_filters:
            placeholders = ", ".join(["%s"] * len(filters[k]))
            where.append(f"`{k}` IN ({placeholders})")
            params.extend(filters[k])
        else:
            where.append(f"`{k}` = %s")
            params.append(filters[k])

    if cursor is not None:
        ph = ", ".join(["%s"] * len(ds.sort_columns))
        where.append(f"({sort}) > ({ph})")
        params.extend(cursor)

    where_clause = " AND ".join(where) if where else "1=1"
    sql = (
        f"SELECT {cols} FROM `{ds.table}` "
        f"WHERE {where_clause} "
        f"ORDER BY {sort} "
        f"LIMIT {int(batch_size)}"
    )
    return sql, params


async def iter_batches(
    conn: aiomysql.Connection,
    ds: Dataset,
    filters: dict[str, Any],
    batch_size: int,
) -> AsyncIterator[list[dict[str, Any]]]:
    cursor: tuple[Any, ...] | None = None
    while True:
        sql, params = _build_query(ds, filters, cursor, batch_size)
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
