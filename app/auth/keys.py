import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import select

from app.db.meta.engine import session
from app.db.meta.models import ApiKey

hasher = PasswordHasher()

PREFIX = "ek_live_"


@dataclass(frozen=True)
class IssuedKey:
    full_key: str
    key_id: str
    secret_hash: str


def make_pair() -> tuple[str, str, str]:
    """Generate (key_id, secret, full_key) for a new API key."""
    key_id = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    return key_id, secret, f"{PREFIX}{key_id}_{secret}"


def split_key(full_key: str) -> tuple[str, str] | None:
    """Parse a full API key into (key_id, secret), or None if malformed."""
    if not full_key.startswith(PREFIX):
        return None
    key_id, _, secret = full_key[len(PREFIX):].partition("_")
    if not key_id or not secret:
        return None
    return key_id, secret


async def verify(full_key: str) -> ApiKey | None:
    parts = split_key(full_key)
    if parts is None:
        return None
    key_id, secret = parts
    async with session() as s:
        row = (
            await s.execute(
                select(ApiKey).where(
                    ApiKey.key_id == key_id,
                    ApiKey.disabled_at.is_(None),
                )
            )
        ).scalar_one_or_none()
    if row is None:
        return None
    if row.expires_at is not None and row.expires_at <= datetime.now(timezone.utc):
        return None
    try:
        hasher.verify(row.secret_hash, secret)
    except VerifyMismatchError:
        return None
    return row
