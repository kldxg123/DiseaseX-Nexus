from __future__ import annotations


def clamp_score(value: float | int | None, default: float = 0.0) -> float:
    if value is None:
        value = default
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        numeric_value = default
    return max(0.0, min(1.0, numeric_value))


def calculate_source_score(
    authority_score: float | None = None,
    relevance_score: float | None = None,
    freshness_score: float | None = None,
    structured_data_score: float | None = None,
    historical_success_score: float | None = None,
    language_country_match: float | None = None,
    legal_or_robots_penalty: float | None = None,
) -> float:
    score = (
        0.30 * clamp_score(authority_score)
        + 0.25 * clamp_score(relevance_score)
        + 0.15 * clamp_score(freshness_score)
        + 0.10 * clamp_score(structured_data_score)
        + 0.10 * clamp_score(historical_success_score)
        + 0.10 * clamp_score(language_country_match)
        - 0.30 * clamp_score(legal_or_robots_penalty)
    )
    return round(clamp_score(score), 6)
