"""Bootstrap entrypoint for local dev: seed/ensure the bootstrap admin key.

Usage (one-shot, run after `alembic upgrade head`):
    uv run python -m app.bootstrap

The bootstrap key is always:
    - is_admin = true
    - datasets = ["*"]
    - expires_at = NULL  (never expires)

Use this key to issue real keys via POST /v1/admin/api-keys.
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
            # ensure admin + * scope even on older seed rows
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
                secret_hash=_hasher.hash(secret),
                label="bootstrap-dev",
                datasets=["*"],
                is_admin=True,
            )
        )
        await s.commit()
        print(f"seeded admin api key {key_id}")


if __name__ == "__main__":
    asyncio.run(_main())
