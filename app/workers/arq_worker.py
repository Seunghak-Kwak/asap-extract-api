from arq.connections import RedisSettings

from app.config import settings
from app.extract import pipeline
from app.observability.logging import configure as configure_logging


async def run_extract(ctx: dict, job_id: str) -> None:  # type: ignore[type-arg]
    await pipeline.run(job_id)


async def startup(ctx: dict) -> None:  # type: ignore[type-arg]
    configure_logging()


class WorkerSettings:
    functions = [run_extract]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(settings().redis_dsn)
    max_jobs = 4
    job_timeout = 60 * 60  # 1 hour per extract; tune later
    keep_result = 0  # we keep our own state in PG; no need to keep in Redis
