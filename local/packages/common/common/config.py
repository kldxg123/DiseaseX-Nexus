from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
except Exception:  # pragma: no cover - exercised only when optional deps are absent
    BaseSettings = None  # type: ignore[assignment]
    SettingsConfigDict = None  # type: ignore[assignment]


if BaseSettings is not None:

    class Settings(BaseSettings):
        model_config = SettingsConfigDict(
            env_file=".env",
            env_file_encoding="utf-8",
            extra="ignore",
        )

        app_env: str = "local"
        app_host: str = "0.0.0.0"
        app_port: int = 8000
        log_level: str = "INFO"

        postgres_host: str = "postgres"
        postgres_port: int = 5432
        postgres_db: str = "infectious_intel"
        postgres_user: str = "infectious_intel"
        postgres_password: str = 'REDACTED'

        redis_host: str = "redis"
        redis_port: int = 6379

        minio_endpoint: str = "http://minio:9000"
        minio_bucket: str = "infectious-intel"
        minio_access_key: str = "minio-local"
        minio_secret_key: str = 'REDACTED'

        mlflow_tracking_uri: str = "http://mlflow:5000"

        crawler_user_agent: str = "infectious-intel-local/0.1"
        crawler_default_rate_limit_seconds: int = 5
        crawler_max_retries: int = 3
        crawler_retry_backoff_seconds: int = 2
        crawler_respect_robots_tx: bool = True

else:

    @dataclass(frozen=True)
    class Settings:
        app_env: str
        app_host: str
        app_port: int
        log_level: str
        postgres_host: str
        postgres_port: int
        postgres_db: str
        postgres_user: str
        postgres_password: str
        redis_host: str
        redis_port: int
        minio_endpoint: str
        minio_bucket: str
        minio_access_key: str
        minio_secret_key: str
        mlflow_tracking_uri: str
        crawler_user_agent: str
        crawler_default_rate_limit_seconds: int
        crawler_max_retries: int
        crawler_retry_backoff_seconds: int
        crawler_respect_robots_tx: bool


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _fallback_settings() -> Settings:
    return Settings(
        app_env=os.getenv("APP_ENV", "local"),
        app_host=os.getenv("APP_HOST", "0.0.0.0"),
        app_port=_env_int("APP_PORT", 8000),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        postgres_host=os.getenv("POSTGRES_HOST", "postgres"),
        postgres_port=_env_int("POSTGRES_PORT", 5432),
        postgres_db=os.getenv("POSTGRES_DB", "infectious_intel"),
        postgres_user=os.getenv("POSTGRES_USER", "infectious_intel"),
        postgres_password=os.getenv("POSTGRES_PASSWORD", "change-me-locally"),
        redis_host=os.getenv("REDIS_HOST", "redis"),
        redis_port=_env_int("REDIS_PORT", 6379),
        minio_endpoint=os.getenv("MINIO_ENDPOINT", "http://minio:9000"),
        minio_bucket=os.getenv("MINIO_BUCKET", "infectious-intel"),
        minio_access_key=os.getenv("MINIO_ACCESS_KEY", "minio-local"),
        minio_secret_key=os.getenv("MINIO_SECRET_KEY", "change-me-locally"),
        mlflow_tracking_uri=os.getenv("MLFLOW_TRACKING_URI", "http://mlflow:5000"),
        crawler_user_agent=os.getenv("CRAWLER_USER_AGENT", "infectious-intel-local/0.1"),
        crawler_default_rate_limit_seconds=_env_int("CRAWLER_DEFAULT_RATE_LIMIT_SECONDS", 5),
        crawler_max_retries=_env_int("CRAWLER_MAX_RETRIES", 3),
        crawler_retry_backoff_seconds=_env_int("CRAWLER_RETRY_BACKOFF_SECONDS", 2),
        crawler_respect_robots_tx=_env_bool("CRAWLER_RESPECT_ROBOTS_TX", True),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    if BaseSettings is not None:
        return Settings()
    return _fallback_settings()
