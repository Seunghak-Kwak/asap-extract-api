from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiomysql

from app.config import settings


@asynccontextmanager
async def source_connection() -> AsyncIterator[aiomysql.Connection]:
    """One dedicated connection per extract job.

    Not pooled: extract queries are long-running and would block other jobs if
    they shared a pool. The connection is created on demand, closed on exit.
    """
    s = settings()
    conn = await aiomysql.connect(
        host=s.source_host,
        port=s.source_port,
        user=s.source_user,
        password=s.source_password,
        db=s.source_db,
        autocommit=True,
        charset="utf8mb4",
    )
    try:
        yield conn
    finally:
        conn.close()
