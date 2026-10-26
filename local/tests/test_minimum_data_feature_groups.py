from __future__ import annotations

from datetime import date

import pytest

from predictor_agent.dataset import FeatureRow
from predictor_agent.minimum_data_analyzer import (
    compute_feature_group_cost,
    filter_feature_json,
    filter_rows_by_feature_groups,
    get_default_feature_groups,
    mask_records_to_feature_groups,
    select_feature_groups,
    truncate_records_by_history_weeks,
)


def _row() -> FeatureRow:
    return FeatureRow(
        id=1,
        country="United States",
        admin1=None,
        disease="norovirus",
        target_date=date(2025, 1, 6),
        iso_week="2025-W01",
        horizon_days=7,
        feature_json={
            "current_week_cases": 10,
            "lag_1w_cases": 9,
            "media_document_count": 2,
            "unused_feature": 99,
        },
        target_value=10.0,
        target_outbreak_label=True,
        split_type="train",
    )


def test_default_feature_groups_include_expected_groups() -> None:
    groups = get_default_feature_groups()

    assert {
        "case_history",
        "case_counts",
        "outbreak_history",
        "outbreak_counts",
        "source_volume",
        "media_signals",
        "official_signals",
        "quality",
        "data_quality",
        "seasonality",
        "holiday",
    }.issubset(groups)
    assert all(0.0 <= group.cost <= 1.0 for group in groups.values())
    assert all(0.0 <= group.missingness <= 1.0 for group in groups.values())
    assert all(0.0 <= group.legal_risk <= 1.0 for group in groups.values())


def test_select_feature_groups_supports_names_and_rejects_unknown() -> None:
    groups = get_default_feature_groups()

    selected = select_feature_groups(groups, ["case_counts", "media_signals"])

    assert [group.name for group in selected] == ["case_counts", "media_signals"]
    with pytest.raises(ValueError, match="Unknown feature group"):
        select_feature_groups(groups, ["unknown_group"])


def test_filter_rows_by_feature_groups_does_not_mutate_original_rows() -> None:
    groups = get_default_feature_groups()
    selected = select_feature_groups(groups, ["case_counts"])
    rows = [_row()]

    filtered = filter_rows_by_feature_groups(rows, selected)

    assert filtered is not rows
    assert filtered[0] is not rows[0]
    assert "unused_feature" in rows[0].feature_json
    assert "unused_feature" not in filtered[0].feature_json
    assert set(filtered[0].feature_json).issubset(set(groups["case_counts"].feature_keys))
    assert filtered[0].feature_json["lag_2w_cases"] == 0.0
    assert filtered[0].country == rows[0].country
    assert filtered[0].target_value == rows[0].target_value


def test_stable_feature_masking_helpers_preserve_original_rows() -> None:
    groups = get_default_feature_groups()
    rows = [_row()]

    assert filter_feature_json(rows[0].feature_json, {"current_week_cases"}) == {"current_week_cases": 10}
    masked = mask_records_to_feature_groups(rows, ["case_history"], groups)
    truncated = truncate_records_by_history_weeks(rows, 1)
    cost = compute_feature_group_cost(["case_history", "quality"], groups)

    assert "unused_feature" in rows[0].feature_json
    assert "unused_feature" not in masked[0].feature_json
    assert masked[0].feature_json["current_week_cases"] == 10
    assert truncated == rows
    assert cost > 0
