from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=True)

    EPIDEMIC_QUERY_API_TOKEN: SecretStr = Field(min_length=32)
    APP_DATABASE_URL: SecretStr
