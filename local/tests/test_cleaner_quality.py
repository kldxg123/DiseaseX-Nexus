from __future__ import annotations

from cleaner_agent.quality import clamp_quality, compute_event_quality_score


def test_quality_score_is_clamped_and_rewards_complete_events() -> None:
    low = compute_event_quality_score(
        has_disease=True,
        has_country=False,
        has_date=False,
        has_numeric_signal=False,
    )
    high = compute_event_quality_score(
        has_disease=True,
        has_country=True,
        has_date=True,
        has_numeric_signal=True,
        source_reliability=0.9,
        extraction_confidence=0.9,
    )

    assert 0 <= low <= 1
    assert 0 <= high <= 1
    assert high > low
    assert clamp_quality(2.0) == 1.0
    assert clamp_quality(-1.0) == 0.0
