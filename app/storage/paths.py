"""On-disk layout for extract results.

    <EXTRACT_DIR>/<yyyy-mm-dd>/<job_id>/result.csv.part   while running
    <EXTRACT_DIR>/<yyyy-mm-dd>/<job_id>/result.csv        on success (atomic rename)

The date partition uses the job's UTC created_at — same value the API
records, so the layout is stable for the lifetime of a job. Operators can
back up or wipe one day at a time:  rm -rf data/extracts/2026-05-01

Nginx serves files via the configured internal prefix, e.g.
    X-Accel-Redirect: /_internal/extracts/2026-05-25/<job_id>/result.csv
The internal location uses `alias /var/lib/extracts/` so nested paths just work.
"""

import os
import shutil
from datetime import datetime
from pathlib import Path

from app.config import settings


def _date_part(created_at: datetime) -> str:
    return created_at.strftime("%Y-%m-%d")


def job_dir(job_id: str, created_at: datetime) -> Path:
    return Path(settings().extract_dir) / _date_part(created_at) / job_id


def partial_path(job_id: str, created_at: datetime, ext: str = "csv") -> Path:
    return job_dir(job_id, created_at) / f"result.{ext}.part"


def final_path(job_id: str, created_at: datetime, ext: str = "csv") -> Path:
    return job_dir(job_id, created_at) / f"result.{ext}"


def internal_url(job_id: str, created_at: datetime, ext: str = "csv") -> str:
    prefix = settings().download_internal_prefix.rstrip("/")
    return f"{prefix}/{_date_part(created_at)}/{job_id}/result.{ext}"


def ensure_job_dir(job_id: str, created_at: datetime) -> Path:
    d = job_dir(job_id, created_at)
    d.mkdir(parents=True, exist_ok=True)
    return d


def atomic_promote(job_id: str, created_at: datetime, ext: str = "csv") -> Path:
    src = partial_path(job_id, created_at, ext)
    dst = final_path(job_id, created_at, ext)
    fd = os.open(src, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    os.rename(src, dst)
    return dst


def cleanup_job(job_id: str, created_at: datetime) -> None:
    """Remove the job's directory. Also drops the date folder if empty."""
    d = job_dir(job_id, created_at)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    try:
        d.parent.rmdir()  # only succeeds when the date folder is empty
    except OSError:
        pass
