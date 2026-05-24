"""Periodic retention sweeper.

For each succeeded job whose `expires_at` has passed, deletes the on-disk
directory and flips `status` to `expired` so the row is preserved (audit)
but downloads are 404. Failed jobs are NOT swept — their `.part` files
stay for forensics until an operator clears them manually.
"""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db.meta.engine import session
from app.db.meta.models import Job, JobStatus
from app.observability.logging import log
from app.storage import paths


async def sweep_expired(ctx: dict[str, Any]) -> int:
    now = datetime.now(timezone.utc)
    swept = 0
    async with session() as s:
        rows = (
            await s.execute(
                select(Job).where(
                    Job.status == JobStatus.succeeded,
                    Job.expires_at.is_not(None),
                    Job.expires_at < now,
                )
            )
        ).scalars().all()
        for job in rows:
            paths.cleanup_job(job.id, job.created_at)
            job.status = JobStatus.expired
            swept += 1
        if swept:
            await s.commit()
    if swept:
        log().info("sweep_expired", count=swept)
    return swept
