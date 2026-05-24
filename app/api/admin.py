from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from app.api.deps import AdminDep
from app.auth.keys import _hasher, _make_pair
from app.db.meta.engine import session
from app.db.meta.models import ApiKey, Job, JobStatus
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


# ---- monitoring -------------------------------------------------------------


class StatsResponse(BaseModel):
    jobs_by_status: dict[str, int]
    rows_extracted_total: int
    active_keys: int
    jobs_last_24h: int


@router.get("/stats", response_model=StatsResponse)
async def stats(_admin: ApiKey = AdminDep) -> StatsResponse:
    now = datetime.now(timezone.utc)
    async with session() as s:
        by_status_rows = (
            await s.execute(
                select(Job.status, func.count()).group_by(Job.status)
            )
        ).all()
        total_rows = (
            await s.execute(select(func.coalesce(func.sum(Job.row_count), 0)))
        ).scalar_one()
        active_keys = (
            await s.execute(
                select(func.count())
                .select_from(ApiKey)
                .where(
                    ApiKey.disabled_at.is_(None),
                    (ApiKey.expires_at.is_(None)) | (ApiKey.expires_at > now),
                )
            )
        ).scalar_one()
        recent = (
            await s.execute(
                select(func.count())
                .select_from(Job)
                .where(Job.created_at >= now - timedelta(hours=24))
            )
        ).scalar_one()
    return StatsResponse(
        jobs_by_status={str(s_): int(n) for s_, n in by_status_rows},
        rows_extracted_total=int(total_rows),
        active_keys=int(active_keys),
        jobs_last_24h=int(recent),
    )


class JobInfo(BaseModel):
    job_id: str
    key_id: str
    key_label: str
    dataset: str
    status: JobStatus
    row_count: int
    bytes: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_class: str | None


@router.get("/extracts", response_model=list[JobInfo])
async def list_extracts(
    api_key_id: str | None = Query(default=None, description="filter by public key_id"),
    job_status: JobStatus | None = Query(default=None, alias="status"),
    dataset: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _admin: ApiKey = AdminDep,
) -> list[JobInfo]:
    q = (
        select(Job, ApiKey.key_id, ApiKey.label)
        .join(ApiKey, ApiKey.id == Job.api_key_id)
        .order_by(Job.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if api_key_id is not None:
        q = q.where(ApiKey.key_id == api_key_id)
    if job_status is not None:
        q = q.where(Job.status == job_status)
    if dataset is not None:
        q = q.where(Job.dataset == dataset)

    async with session() as s:
        rows = (await s.execute(q)).all()

    return [
        JobInfo(
            job_id=j.id,
            key_id=kid,
            key_label=klabel,
            dataset=j.dataset,
            status=JobStatus(j.status),
            row_count=j.row_count,
            bytes=j.bytes,
            created_at=j.created_at,
            started_at=j.started_at,
            finished_at=j.finished_at,
            error_class=j.error_class,
        )
        for j, kid, klabel in rows
    ]
