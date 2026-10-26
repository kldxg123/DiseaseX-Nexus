from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
except Exception:  # pragma: no cover - used only before dependencies are installed
    BaseSettings = None  # type: ignore[assignment]
    SettingsConfigDict = None  # type: ignore[assignment]


if BaseSettings is not None:

    class Settings(BaseSettings):
        model_config = SettingsConfigDict(
            extra="ignore",
        )

        app_env: str = "development"
        api_host: str = "0.0.0.0"
        api_port: int = 8000
        database_url: str = (
            "postgresql://REDACTED@postgres:5432/infectious_intel"
        )
        redis_url: str = "redis://redis:6379/0"
        minio_endpoint: str = "http://minio:9000"
        minio_access_key: str = "minio-local"
        minio_secret_key: str = 'REDACTED'
        mlflow_tracking_uri: str = "http://mlflow:5000"
        crawler_user_agent: str = "infectious-intel-local/0.1"
        crawler_respect_robots: bool = True
        crawler_request_timeout_seconds: int = 30

else:

    @dataclass(frozen=True)
    class Settings:
        app_env: str
        api_host: str
        api_port: int
        database_url: str
        redis_url: str
        minio_endpoint: str
        minio_access_key: str
        minio_secret_key: str
        mlflow_tracking_uri: str
        crawler_user_agent: str
        crawler_respect_robots: bool
        crawler_request_timeout_seconds: int


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
        app_env=os.getenv("APP_ENV", "development"),
        api_host=os.getenv("API_HOST", "0.0.0.0"),
        api_port=_env_int("API_PORT", 8000),
        database_url=os.getenv(
            "DATABASE_URL",
            "postgresql://REDACTED@postgres:5432/infectious_intel",
        ),
        redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0"),
        minio_endpoint=os.getenv("MINIO_ENDPOINT", "http://minio:9000"),
        minio_access_key=os.getenv("MINIO_ACCESS_KEY", "minio-local"),
        minio_secret_key=os.getenv("MINIO_SECRET_KEY", "change-me-locally"),
        mlflow_tracking_uri=os.getenv("MLFLOW_TRACKING_URI", "http://mlflow:5000"),
        crawler_user_agent=os.getenv("CRAWLER_USER_AGENT", "infectious-intel-local/0.1"),
        crawler_respect_robots=_env_bool("CRAWLER_RESPECT_ROBOTS", True),
        crawler_request_timeout_seconds=_env_int("CRAWLER_REQUEST_TIMEOUT_SECONDS", 30),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    if BaseSettings is not None:
        return Settings()
    return _fallback_settings()
