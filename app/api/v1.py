import uuid
from datetime import datetime
from typing import Any

from arq.connections import ArqRedis, RedisSettings, create_pool
from fastapi import APIRouter, Header, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.api.deps import ApiKeyDep
from app.config import settings
from app.db.meta.engine import session
from app.db.meta.models import ApiKey, Job, JobStatus
from app.extract import registry
from app.storage import paths

router = APIRouter(prefix="/v1")


class ExtractCreate(BaseModel):
    dataset: str
    filters: dict[str, Any]
    format: str = Field(default="csv", pattern="^csv$")


class ExtractResponse(BaseModel):
    job_id: str
    status: JobStatus
    dataset: str
    row_count: int
    bytes: int
    file_sha256: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    expires_at: datetime | None
    error_class: str | None
    error_message: str | None


def _to_response(job: Job) -> ExtractResponse:
    return ExtractResponse(
        job_id=job.id,
        status=JobStatus(job.status),
        dataset=job.dataset,
        row_count=job.row_count,
        bytes=job.bytes,
        file_sha256=job.file_sha256,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        expires_at=job.expires_at,
        error_class=job.error_class,
        error_message=job.error_message,
    )


_arq_pool: ArqRedis | None = None


async def _arq() -> ArqRedis:
    global _arq_pool
    if _arq_pool is None:
        _arq_pool = await create_pool(RedisSettings.from_dsn(settings().redis_dsn))
    return _arq_pool


@router.post("/extracts", response_model=ExtractResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_extract(
    body: ExtractCreate,
    key: ApiKey = ApiKeyDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ExtractResponse:
    # validate dataset + filters up front so we fail loudly before we enqueue
    ds = registry.get(body.dataset)
    if not key.allows_dataset(body.dataset):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"this api key is not scoped to dataset '{body.dataset}'",
        )
    try:
        registry.validate_filters(ds, body.filters)
    except registry.ExtractValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    job_id = str(uuid.uuid4())
    job = Job(
        id=job_id,
        api_key_id=key.id,
        idempotency_key=idempotency_key,
        dataset=body.dataset,
        filters=body.filters,
        format=body.format,
    )
    async with session() as s:
        s.add(job)
        try:
            await s.commit()
        except IntegrityError:
            # idempotency replay
            await s.rollback()
            existing = (
                await s.execute(
                    Job.__table__.select().where(
                        (Job.api_key_id == key.id)
                        & (Job.idempotency_key == idempotency_key)
                    )
                )
            ).first()
            if existing is None:
                raise
            row = await s.get(Job, existing.id)
            assert row is not None
            return _to_response(row)
        await s.refresh(job)

    pool = await _arq()
    await pool.enqueue_job("run_extract", job_id, _job_id=f"job:{job_id}")
    return _to_response(job)


@router.get("/extracts/{job_id}", response_model=ExtractResponse)
async def get_extract(job_id: str, key: ApiKey = ApiKeyDep) -> ExtractResponse:
    async with session() as s:
        job = await s.get(Job, job_id)
    if job is None or job.api_key_id != key.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    return _to_response(job)


@router.delete("/extracts/{job_id}", status_code=status.HTTP_202_ACCEPTED)
async def cancel_extract(job_id: str, key: ApiKey = ApiKeyDep) -> dict[str, str]:
    async with session() as s:
        job = await s.get(Job, job_id)
        if job is None or job.api_key_id != key.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
        if job.status in (JobStatus.succeeded, JobStatus.failed, JobStatus.cancelled):
            return {"status": job.status}
        await s.execute(
            update(Job).where(Job.id == job_id).values(cancel_requested=True)
        )
        await s.commit()
    return {"status": "cancel_requested"}


@router.get("/extracts/{job_id}/download")
async def download_extract(job_id: str, key: ApiKey = ApiKeyDep) -> Response:
    async with session() as s:
        job = await s.get(Job, job_id)
    if job is None or job.api_key_id != key.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    if job.status != JobStatus.succeeded:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"job is {job.status}, not downloadable",
        )

    filename = f"{job.dataset}-{job.id}.{job.format}"
    headers = {
        "X-Accel-Redirect": paths.internal_url(job.id, job.format),
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Type": "text/csv; charset=utf-8",
    }
    return Response(status_code=200, headers=headers)
