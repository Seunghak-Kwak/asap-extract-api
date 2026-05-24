"""The extract pipeline — the one place where every layer meets.

A single function: given a job_id, load the job row, run the extract, update
the job row. The router and the worker both treat this as a black box.

Memory rule: only one batch's worth of rows is alive at any time. The file on
disk grows; nothing else does.
"""

import hashlib
from datetime import datetime, timedelta, timezone
from typing import BinaryIO

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
    """Wraps a binary file so every write also feeds a hash + a byte counter."""

    def __init__(self, fh: BinaryIO, hasher: "hashlib._Hash") -> None:
        self._fh = fh
        self._hasher = hasher
        self.bytes_written = 0

    def write(self, data: bytes) -> int:
        self._hasher.update(data)
        n = self._fh.write(data)
        self.bytes_written += n
        return n

    def flush(self) -> None:
        self._fh.flush()


async def _load_job(job_id: str) -> Job:
    async with session() as s:
        job = await s.get(Job, job_id)
        if job is None:
            raise LookupError(f"job {job_id} not found")
        return job


async def _claim(job_id: str) -> None:
    async with session() as s:
        await s.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == JobStatus.queued)
            .values(status=JobStatus.running, started_at=datetime.now(timezone.utc))
        )
        await s.commit()


async def _check_cancel(job_id: str) -> bool:
    async with session() as s:
        flag = (
            await s.execute(select(Job.cancel_requested).where(Job.id == job_id))
        ).scalar_one()
        return bool(flag)


async def _mark_succeeded(
    job_id: str, row_count: int, byte_count: int, sha: str
) -> None:
    s_ = settings()
    expires = datetime.now(timezone.utc) + timedelta(hours=s_.extract_retention_hours)
    async with session() as s:
        await s.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                status=JobStatus.succeeded,
                row_count=row_count,
                bytes=byte_count,
                file_sha256=sha,
                finished_at=datetime.now(timezone.utc),
                expires_at=expires,
            )
        )
        await s.commit()


async def _mark_failed(job_id: str, exc: BaseException) -> None:
    async with session() as s:
        await s.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                status=JobStatus.failed,
                error_class=type(exc).__name__,
                error_message=str(exc)[:2000],
                finished_at=datetime.now(timezone.utc),
            )
        )
        await s.commit()


async def _mark_cancelled(job_id: str) -> None:
    async with session() as s:
        await s.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(status=JobStatus.cancelled, finished_at=datetime.now(timezone.utc))
        )
        await s.commit()


async def run(job_id: str) -> None:
    log_ = log().bind(job_id=job_id)
    job = await _load_job(job_id)
    ds = registry.get(job.dataset)
    filters = registry.validate_filters(ds, job.filters)

    s_ = settings()
    paths.ensure_job_dir(job_id)
    part = paths.partial_path(job_id)

    await _claim(job_id)
    extracts_started.labels(dataset=ds.name).inc()
    started = datetime.now(timezone.utc)
    row_count = 0
    hasher = hashlib.sha256()

    try:
        with extract_duration.labels(dataset=ds.name).time():
            with open(part, "wb") as raw_fh:
                hashing = _HashingFile(raw_fh, hasher)
                writer = CsvBatchWriter(hashing, ds.columns)  # type: ignore[arg-type]
                async with source_connection() as conn:
                    async for batch in paginator.iter_batches(
                        conn, ds, filters, s_.extract_batch_size
                    ):
                        if await _check_cancel(job_id):
                            raise ExtractCancelled()
                        writer.write_batch(batch)
                        raw_fh.flush()
                        row_count += len(batch)
                        if row_count > s_.extract_max_rows:
                            raise ExtractTooLarge(
                                f"exceeded max rows ({s_.extract_max_rows})"
                            )
                byte_count = hashing.bytes_written

        paths.atomic_promote(job_id)
        await _mark_succeeded(job_id, row_count, byte_count, hasher.hexdigest())
        extracts_finished.labels(dataset=ds.name, status="succeeded").inc()
        extract_rows.labels(dataset=ds.name).inc(row_count)
        log_.info(
            "extract_succeeded",
            dataset=ds.name,
            rows=row_count,
            bytes=byte_count,
            elapsed_s=(datetime.now(timezone.utc) - started).total_seconds(),
        )
    except ExtractCancelled:
        paths.cleanup_job(job_id)
        await _mark_cancelled(job_id)
        extracts_finished.labels(dataset=ds.name, status="cancelled").inc()
        log_.info("extract_cancelled", dataset=ds.name, rows=row_count)
    except Exception as exc:
        await _mark_failed(job_id, exc)
        extracts_finished.labels(dataset=ds.name, status="failed").inc()
        log_.error("extract_failed", dataset=ds.name, error=str(exc))
        raise
