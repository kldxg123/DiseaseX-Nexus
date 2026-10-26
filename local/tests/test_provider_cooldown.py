from __future__ import annotations

from source_discovery_agent.live_providers.cooldown import recommend_provider_cooldown


def test_rate_limited_provider_gets_default_cooldown() -> None:
    result = recommend_provider_cooldown(provider="gdelt", failure_type="rate_limited")

    assert result.cooldown_recommended is True
    assert result.retry_after_seconds is None
    assert result.minimum_cooldown_minutes >= 60
    assert "do not retry" in result.reason.lower()


def test_retry_after_seconds_controls_cooldown_minutes() -> None:
    result = recommend_provider_cooldown(provider="gdelt", failure_type="rate_limited", retry_after_seconds=120)

    assert result.cooldown_recommended is True
    assert result.retry_after_seconds == 120
    assert result.minimum_cooldown_minutes == 2


def test_non_rate_limited_failure_has_no_cooldown() -> None:
    result = recommend_provider_cooldown(provider="gdelt", failure_type="http_error")

    assert result.cooldown_recommended is False
    assert result.minimum_cooldown_minutes == 0
