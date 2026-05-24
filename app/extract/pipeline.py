"""The extract pipeline — the one place where every layer meets.

Memory rule: only one batch's worth of rows is alive at any time. The file on
disk grows; nothing else does.
"""

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, BinaryIO

from sqlalchemy import select, update

from app.config import settings
from app.db.meta.engine import session
from app.db.meta.models import Job, JobStatus
from app.db.source.connection import source_connection
from app.extract import paginator, registry
from app.extract.writer import CsvBatchWriter
from app.observability.logging import log
from app.observability.metrics import (
    extract_duration,
    extract_rows,
    extracts_finished,
    extracts_started,
)
from app.storage import paths


class ExtractCancelled(Exception):
    pass


class ExtractTooLarge(Exception):
    pass


class _HashingFile:
    def __init__(self, fh: BinaryIO, h: "hashlib._Hash") -> None:
        self._fh = fh
        self._hasher = h
        self.bytes_written = 0

    def write(self, data: bytes) -> int:
        self._hasher.update(data)
        n = self._fh.write(data)
        self.bytes_written += n
        return n


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _load_job(job_id: str) -> Job:
    async with session() as s:
        job = await s.get(Job, job_id)
        if job is None:
            raise LookupError(f"job {job_id} not found")
        return job


async def _claim(job_id: str) -> bool:
    """Atomic claim: returns True only on the transition queued→running.
    False means the job is already running/done/failed (retry no-op)."""
    async with session() as s:
        result = await s.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == JobStatus.queued)
            .values(status=JobStatus.running, started_at=_utcnow())
        )
        await s.commit()
        return result.rowcount > 0


async def _check_cancel(job_id: str) -> bool:
    async with session() as s:
        flag = (
            await s.execute(select(Job.cancel_requested).where(Job.id == job_id))
        ).scalar_one()
        return bool(flag)


async def _update_job(job_id: str, **values: Any) -> None:
    async with session() as s:
        await s.execute(update(Job).where(Job.id == job_id).values(**values))
        await s.commit()


async def run(job_id: str) -> None:
    log_ = log().bind(job_id=job_id)
    job = await _load_job(job_id)
    ds = registry.get(job.dataset)
    filters = registry.validate_filters(ds, job.filters)

    cfg = settings()
    paths.ensure_job_dir(job_id, job.created_at)
    part = paths.partial_path(job_id, job.created_at)

    if not await _claim(job_id):
        log_.info("extract_skip_not_queued", status=job.status)
        return
    extracts_started.labels(dataset=ds.name).inc()
    started = _utcnow()
    row_count = 0
    hasher = hashlib.sha256()

    try:
        with extract_duration.labels(dataset=ds.name).time():
            with open(part, "wb") as raw_fh:
                hashing = _HashingFile(raw_fh, hasher)
                writer = CsvBatchWriter(hashing, ds.columns)  # type: ignore[arg-type]
                async with source_connection() as conn:
                    async for batch in paginator.iter_batches(
                        conn, ds, filters, cfg.extract_batch_size
                    ):
                        if await _check_cancel(job_id):
                            raise ExtractCancelled()
                        writer.write_batch(batch)
                        row_count += len(batch)
                        if row_count > cfg.extract_max_rows:
                            raise ExtractTooLarge(
                                f"exceeded max rows ({cfg.extract_max_rows})"
                            )
                byte_count = hashing.bytes_written

        paths.atomic_promote(job_id, job.created_at)
        await _update_job(
            job_id,
            status=JobStatus.succeeded,
            row_count=row_count,
            bytes=byte_count,
            file_sha256=hasher.hexdigest(),
            finished_at=_utcnow(),
            expires_at=_utcnow() + timedelta(hours=cfg.extract_retention_hours),
        )
        extracts_finished.labels(dataset=ds.name, status="succeeded").inc()
        extract_rows.labels(dataset=ds.name).inc(row_count)
        log_.info(
            "extract_succeeded",
            dataset=ds.name,
            rows=row_count,
            bytes=byte_count,
            elapsed_s=(_utcnow() - started).total_seconds(),
        )
    except ExtractCancelled:
        paths.cleanup_job(job_id, job.created_at)
        await _update_job(job_id, status=JobStatus.cancelled, finished_at=_utcnow())
        extracts_finished.labels(dataset=ds.name, status="cancelled").inc()
        log_.info("extract_cancelled", dataset=ds.name, rows=row_count)
    except Exception as exc:
        await _update_job(
            job_id,
            status=JobStatus.failed,
            error_class=type(exc).__name__,
            error_message=str(exc)[:2000],
            finished_at=_utcnow(),
        )
        extracts_finished.labels(dataset=ds.name, status="failed").inc()
        log_.error("extract_failed", dataset=ds.name, error=str(exc))
        raise
