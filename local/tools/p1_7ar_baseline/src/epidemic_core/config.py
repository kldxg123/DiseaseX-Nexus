from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="forbid", case_sensitive=True)

    APP_DATABASE_URL: SecretStr | None = None
    MIGRATION_DATABASE_URL: SecretStr | None = None
    RAW_ARTIFACT_ROOT: Path = Path("USER_HOME/autodl-tmp/epidemic-openclaw/storage/raw/sha256")
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 5
    DB_POOL_RECYCLE_SECONDS: int = 1800
