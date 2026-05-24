from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    log_level: str = "INFO"

    pg_dsn: str
    source_host: str
    source_port: int = 3306
    source_user: str
    source_password: str
    source_db: str

    redis_dsn: str

    extract_dir: str = "/var/lib/extracts"
    download_internal_prefix: str = "/_internal/extracts"

    extract_batch_size: int = Field(default=10000, ge=100, le=100000)
    extract_max_rows: int = Field(default=50_000_000, ge=1)
    extract_retention_hours: int = Field(default=72, ge=1)
    extract_max_inflight_per_key: int = Field(default=5, ge=1, le=1000)

    bootstrap_api_key: str | None = None


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
