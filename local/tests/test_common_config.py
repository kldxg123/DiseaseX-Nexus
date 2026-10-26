from __future__ import annotations

from common.config import get_settings


def test_get_settings_defaults() -> None:
    get_settings.cache_clear()
    settings = get_settings()

    assert settings.app_env == "development"
    assert settings.api_port == 8000
    assert settings.crawler_respect_robots is True


def test_get_settings_environment_override(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("API_PORT", "9009")
    monkeypatch.setenv("CRAWLER_RESPECT_ROBOTS", "false")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.app_env == "test"
    assert settings.api_port == 9009
    assert settings.crawler_respect_robots is False
    get_settings.cache_clear()
