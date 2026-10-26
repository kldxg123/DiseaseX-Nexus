from __future__ import annotations

from crawler_agent.source_scoring import calculate_source_score, clamp_score


def test_clamp_score_bounds_and_defaults() -> None:
    assert clamp_score(None, default=0.4) == 0.4
    assert clamp_score(-1) == 0.0
    assert clamp_score(2) == 1.0
    assert clamp_score(0.5) == 0.5


def test_calculate_source_score_uses_weighted_formula() -> None:
    score = calculate_source_score(
        authority_score=1.0,
        relevance_score=0.8,
        freshness_score=0.6,
        structured_data_score=0.4,
        historical_success_score=0.2,
        language_country_match=1.0,
        legal_or_robots_penalty=0.1,
    )

    assert score == 0.72


def test_calculate_source_score_clamps_inputs_and_output() -> None:
    score = calculate_source_score(
        authority_score=2.0,
        relevance_score=-1.0,
        freshness_score=2.0,
        structured_data_score=2.0,
        historical_success_score=2.0,
        language_country_match=2.0,
        legal_or_robots_penalty=-1.0,
    )

    assert score == 0.75


def test_legal_or_robots_penalty_lowers_score() -> None:
    without_penalty = calculate_source_score(
        authority_score=0.8,
        relevance_score=0.8,
        freshness_score=0.8,
        structured_data_score=0.8,
        historical_success_score=0.8,
        language_country_match=0.8,
        legal_or_robots_penalty=0.0,
    )
    with_penalty = calculate_source_score(
        authority_score=0.8,
        relevance_score=0.8,
        freshness_score=0.8,
        structured_data_score=0.8,
        historical_success_score=0.8,
        language_country_match=0.8,
        legal_or_robots_penalty=1.0,
    )

    assert without_penalty == 0.8
    assert with_penalty == 0.5
