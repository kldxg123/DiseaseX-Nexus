from __future__ import annotations

from evidence_discovery_agent import (
    MockEvidenceProvider,
    build_disease_task,
    extract_feature_candidates,
    extract_model_candidates,
)


def test_feature_candidates_map_groups_and_m1_support() -> None:
    task = build_disease_task(
        disease="influenza",
        country="United States",
        prediction_target="outbreak_or_case_trend",
    )
    documents = MockEvidenceProvider().discover(task)

    candidates = extract_feature_candidates(documents)
    groups = {candidate.feature_group: candidate for candidate in candidates}

    assert "epidemiological_lag" in groups
    assert "rolling_statistics" in groups
    assert "weather" in groups
    assert "mobility" in groups
    assert groups["epidemiological_lag"].m1_supported is True
    assert groups["weather"].m1_supported is False
    assert groups["mobility"].gap_notes


def test_model_candidates_rank_baselines_before_transformers() -> None:
    task = build_disease_task(
        disease="influenza",
        country="United States",
        prediction_target="outbreak_or_case_trend",
    )
    documents = MockEvidenceProvider().discover(task)

    candidates = extract_model_candidates(documents)
    by_family = {candidate.model_family: candidate for candidate in candidates}

    assert by_family["baseline"].suitable_for_m2_first_batch is True
    assert by_family["gradient_boosting"].suitable_for_m2_first_batch is True
    assert by_family["transformer"].suitable_for_m2_first_batch is False
    assert "data_hungry" in by_family["transformer"].risks
    assert by_family["baseline"].score > by_family["transformer"].score
