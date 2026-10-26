from __future__ import annotations


def clamp_quality(value: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, parsed))


def compute_event_quality_score(
    *,
    has_disease: bool,
    has_country: bool,
    has_date: bool,
    has_numeric_signal: bool,
    source_reliability: float | None = None,
    extraction_confidence: float | None = None,
) -> float:
    score = 0.05
    if has_disease:
        score += 0.25
    if has_country:
        score += 0.2
    if has_date:
        score += 0.15
    if has_numeric_signal:
        score += 0.2
    if source_reliability is not None:
        score += 0.1 * clamp_quality(source_reliability)
    if extraction_confidence is not None:
        score += 0.05 * clamp_quality(extraction_confidence)
    return clamp_quality(score)
