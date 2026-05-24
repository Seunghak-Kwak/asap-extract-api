"""On-disk layout for extract results.

    <EXTRACT_DIR>/<job_id>/result.csv.part   while running
    <EXTRACT_DIR>/<job_id>/result.csv        on success (atomic rename)

The job_id is a UUID, which gives us a safe per-job directory namespace and
makes cleanup trivial (rm -rf <EXTRACT_DIR>/<job_id>).

Nginx serves files via the configured internal prefix, e.g.
    X-Accel-Redirect: /_internal/extracts/<job_id>/result.csv
"""

import os
import shutil
from pathlib import Path

from app.config import settings


def job_dir(job_id: str) -> Path:
    return Path(settings().extract_dir) / job_id


def partial_path(job_id: str, ext: str = "csv") -> Path:
    return job_dir(job_id) / f"result.{ext}.part"


def final_path(job_id: str, ext: str = "csv") -> Path:
    return job_dir(job_id) / f"result.{ext}"


def internal_url(job_id: str, ext: str = "csv") -> str:
    prefix = settings().download_internal_prefix.rstrip("/")
    return f"{prefix}/{job_id}/result.{ext}"


def ensure_job_dir(job_id: str) -> Path:
    d = job_dir(job_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def atomic_promote(job_id: str, ext: str = "csv") -> Path:
    src = partial_path(job_id, ext)
    dst = final_path(job_id, ext)
    fd = os.open(src, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    os.rename(src, dst)
    return dst


def cleanup_job(job_id: str) -> None:
    d = job_dir(job_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
