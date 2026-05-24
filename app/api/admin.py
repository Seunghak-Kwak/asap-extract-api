from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from app.api.deps import AdminDep
from app.auth.keys import _hasher, _make_pair
from app.db.meta.engine import session
from app.db.meta.models import ApiKey
from app.extract.registry import REGISTRY

router = APIRouter(prefix="/v1/admin", tags=["admin"])


class ApiKeyCreate(BaseModel):
    label: str = Field(min_length=1, max_length=128)
    datasets: list[str] = Field(
        default_factory=list,
        description='List of dataset names this key may extract from. Use ["*"] for all.',
    )
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)
    is_admin: bool = False


class ApiKeyIssued(BaseModel):
    key_id: str
    full_key: str  # shown once; the server keeps only the Argon2id hash
    label: str
    datasets: list[str]
    is_admin: bool
    created_at: datetime
    expires_at: datetime | None


class ApiKeyInfo(BaseModel):
    key_id: str
    label: str
    datasets: list[str]
    is_admin: bool
    created_at: datetime
    expires_at: datetime | None
    disabled_at: datetime | None


def _info(k: ApiKey) -> ApiKeyInfo:
    return ApiKeyInfo(
        key_id=k.key_id,
        label=k.label,
        datasets=k.datasets,
        is_admin=k.is_admin,
        created_at=k.created_at,
        expires_at=k.expires_at,
        disabled_at=k.disabled_at,
    )


def _validate_dataset_scope(datasets: list[str]) -> None:
    if "*" in datasets:
        if len(datasets) != 1:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                'datasets: "*" must be the only entry',
            )
        return
    unknown = [d for d in datasets if d not in REGISTRY]
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"unknown datasets: {unknown}",
        )


@router.post("/api-keys", response_model=ApiKeyIssued, status_code=status.HTTP_201_CREATED)
async def create_key(body: ApiKeyCreate, _admin: ApiKey = AdminDep) -> ApiKeyIssued:
    _validate_dataset_scope(body.datasets)

    key_id, secret, full = _make_pair()
    expires_at: datetime | None = None
    if body.expires_in_days is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)

    row = ApiKey(
        key_id=key_id,
        secret_hash=_hasher.hash(secret),
        label=body.label,
        datasets=body.datasets,
        is_admin=body.is_admin,
        expires_at=expires_at,
    )
    async with session() as s:
        s.add(row)
        await s.commit()
        await s.refresh(row)

    return ApiKeyIssued(
        key_id=row.key_id,
        full_key=full,
        label=row.label,
        datasets=row.datasets,
        is_admin=row.is_admin,
        created_at=row.created_at,
        expires_at=row.expires_at,
    )


@router.get("/api-keys", response_model=list[ApiKeyInfo])
async def list_keys(_admin: ApiKey = AdminDep) -> list[ApiKeyInfo]:
    async with session() as s:
        rows = (await s.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))).scalars().all()
    return [_info(r) for r in rows]


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(key_id: str, admin: ApiKey = AdminDep) -> None:
    if admin.key_id == key_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cannot revoke your own key")
    async with session() as s:
        result = await s.execute(
            update(ApiKey)
            .where(ApiKey.key_id == key_id, ApiKey.disabled_at.is_(None))
            .values(disabled_at=datetime.now(timezone.utc))
        )
        if result.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "key not found or already disabled")
        await s.commit()
