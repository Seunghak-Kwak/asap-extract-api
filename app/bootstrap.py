"""Bootstrap entrypoint for local dev: seed a single API key from env.

Usage (one-shot, run after `alembic upgrade head`):
    uv run python -m app.bootstrap
"""

import asyncio

from sqlalchemy import select

from app.auth.keys import PREFIX, _hasher
from app.config import settings
from app.db.meta.engine import session
from app.db.meta.models import ApiKey


async def _main() -> None:
    raw = settings().bootstrap_api_key
    if not raw:
        print("BOOTSTRAP_API_KEY not set; skipping")
        return
    if not raw.startswith(PREFIX):
        print(f"BOOTSTRAP_API_KEY must start with {PREFIX!r}; skipping")
        return
    rest = raw[len(PREFIX):]
    key_id, _, secret = rest.partition("_")
    if not key_id or not secret:
        print("BOOTSTRAP_API_KEY malformed; skipping")
        return

    async with session() as s:
        existing = (
            await s.execute(select(ApiKey).where(ApiKey.key_id == key_id))
        ).scalar_one_or_none()
        if existing is not None:
            print(f"api key {key_id} already present")
            return
        s.add(
            ApiKey(
                key_id=key_id,
                secret_hash=_hasher.hash(secret),
                label="bootstrap-dev",
            )
        )
        await s.commit()
        print(f"seeded api key {key_id}")


if __name__ == "__main__":
    asyncio.run(_main())
