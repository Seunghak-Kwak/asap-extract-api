from typing import Any

from arq.connections import RedisSettings

from app.config import settings
from app.extract import pipeline
from app.observability.logging import configure as configure_logging


async def run_extract(ctx: dict[str, Any], job_id: str) -> None:
    await pipeline.run(job_id)


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging()


class WorkerSettings:
    functions = [run_extract]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(settings().redis_dsn)
    max_jobs = 4
    job_timeout = 60 * 60
    keep_result = 0  # we own state in Postgres; Redis result store would duplicate
