import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import select

from app.db.meta.engine import session
from app.db.meta.models import ApiKey

_hasher = PasswordHasher()

PREFIX = "ek_live_"


@dataclass(frozen=True)
class IssuedKey:
    full_key: str  # shown to the user once
    key_id: str
    secret_hash: str


def _make_pair() -> tuple[str, str, str]:
    key_id = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    full_key = f"{PREFIX}{key_id}_{secret}"
    return key_id, secret, full_key


def issue() -> IssuedKey:
    key_id, secret, full_key = _make_pair()
    return IssuedKey(
        full_key=full_key,
        key_id=key_id,
        secret_hash=_hasher.hash(secret),
    )


def _split(full_key: str) -> tuple[str, str] | None:
    if not full_key.startswith(PREFIX):
        return None
    rest = full_key[len(PREFIX):]
    key_id, _, secret = rest.partition("_")
    if not key_id or not secret:
        return None
    return key_id, secret


async def verify(full_key: str) -> ApiKey | None:
    parts = _split(full_key)
    if parts is None:
        return None
    key_id, secret = parts
    now = datetime.now(timezone.utc)
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
    if row.expires_at is not None and row.expires_at <= now:
        return None
    try:
        _hasher.verify(row.secret_hash, secret)
    except VerifyMismatchError:
        return None
    return row
