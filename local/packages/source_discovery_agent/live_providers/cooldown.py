from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderCooldownRecommendation:
    provider: str
    failure_type: str
    cooldown_recommended: bool
    retry_after_seconds: int | None
    minimum_cooldown_minutes: int
    reason: str


def recommend_provider_cooldown(
    *,
    provider: str,
    failure_type: str,
    retry_after_seconds: int | None = None,
) -> ProviderCooldownRecommendation:
    normalized_failure = str(failure_type or "unknown").strip().lower()
    parsed_retry_after = _positive_int_or_none(retry_after_seconds)
    if normalized_failure == "rate_limited":
        minutes = max(1, (parsed_retry_after + 59) // 60) if parsed_retry_after else 60
        return ProviderCooldownRecommendation(
            provider=str(provider or "unknown"),
            failure_type="rate_limited",
            cooldown_recommended=True,
            retry_after_seconds=parsed_retry_after,
            minimum_cooldown_minutes=minutes,
            reason="Provider returned a rate-limit response; do not retry immediately.",
        )
    return ProviderCooldownRecommendation(
        provider=str(provider or "unknown"),
        failure_type=normalized_failure or "unknown",
        cooldown_recommended=False,
        retry_after_seconds=parsed_retry_after,
        minimum_cooldown_minutes=0,
        reason="Failure is not classified as rate_limited.",
    )


def _positive_int_or_none(value: int | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
