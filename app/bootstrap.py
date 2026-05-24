"""Seed/ensure the bootstrap admin key from BOOTSTRAP_API_KEY.

Runs on every app container start. Idempotent: if the key already exists,
it is reset to admin/["*"]/no-expiry so dev environments don't drift.
"""

import asyncio

from sqlalchemy import select

from app.auth.keys import hasher, split_key
from app.config import settings
from app.db.meta.engine import session
from app.db.meta.models import ApiKey


async def _main() -> None:
    raw = settings().bootstrap_api_key
    if not raw:
        print("BOOTSTRAP_API_KEY not set; skipping")
        return
    parts = split_key(raw)
    if parts is None:
        print("BOOTSTRAP_API_KEY malformed; skipping")
        return
    key_id, secret = parts

    async with session() as s:
        existing = (
            await s.execute(select(ApiKey).where(ApiKey.key_id == key_id))
        ).scalar_one_or_none()
        if existing is not None:
            existing.is_admin = True
            existing.datasets = ["*"]
            existing.disabled_at = None
            existing.expires_at = None
            await s.commit()
            print(f"api key {key_id} present; ensured admin + *")
            return
        s.add(
            ApiKey(
                key_id=key_id,
                secret_hash=hasher.hash(secret),
                label="bootstrap-dev",
                datasets=["*"],
                is_admin=True,
            )
        )
        await s.commit()
        print(f"seeded admin api key {key_id}")


if __name__ == "__main__":
    asyncio.run(_main())
