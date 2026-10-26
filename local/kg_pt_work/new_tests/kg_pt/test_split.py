# ruff: noqa: E501
"""Temporal split and rolling-origin leakage boundary tests (pure, no DB).

Includes the origin-spec assertions (SPEC test_origins.py is folded in here).
"""

from datetime import date

import pytest

from epidemic_core.forecast_model_kg.dual_loop import period_add
from epidemic_core.kg_pt.split import (
    build_origin_spec,
    make_split,
    tuning_origin_indices,
)
from epidemic_core.kg_pt.split import test_origin_indices as heldout_origin_indices


def test_make_split_boundaries(synthetic_periods):
    split = make_split(synthetic_periods, train_n=168, val_n=36, test_n=36)
    assert split.train_start == date(2005, 1, 1)
    assert split.train_end == date(2018, 12, 1)
    assert split.validation_start == date(2019, 1, 1)
    assert split.validation_end == date(2021, 12, 1)
    assert split.test_start == date(2022, 1, 1)
    assert split.test_end == date(2024, 12, 1)
    assert (split.train_rows, split.validation_rows, split.test_rows) == (168, 36, 36)
    assert split.split_sha256 == make_split(synthetic_periods, 168, 36, 36).split_sha256


def test_split_infeasible_when_series_too_short(synthetic_periods):
    with pytest.raises(ValueError, match="SPLIT_INFEASIBLE"):
        make_split(synthetic_periods[:200], train_n=168, val_n=36, test_n=36)


def test_split_infeasible_when_boundaries_not_strict(synthetic_periods):
    # duplicate the boundary period so train_end == validation_start
    periods = list(synthetic_periods)
    periods[168] = periods[167]
    with pytest.raises(ValueError, match="SPLIT_INFEASIBLE"):
        make_split(periods, train_n=168, val_n=36, test_n=36)


def test_split_empty_test_allowed_at_demo_end(synthetic_periods):
    split = make_split(synthetic_periods, train_n=204, val_n=36, test_n=0)
    assert split.validation_end == date(2024, 12, 1)
    assert split.test_start is None and split.test_end is None
    assert split.test_rows == 0


def test_tuning_origin_indices(synthetic_periods):
    split = make_split(synthetic_periods)
    origins = tuning_origin_indices(204, split)
    assert origins == [155, 159, 163, 167, 171, 175, 179, 183, 187, 191, 195, 199]
    assert len(origins) == 12
    for idx in origins:
        assert idx >= 59
        assert idx + 4 <= 168 + 36 - 1  # labels stay inside train+validation


def test_test_origin_indices(synthetic_periods):
    split = make_split(synthetic_periods)
    origins = heldout_origin_indices(240, split)
    assert origins == [203, 207, 211, 215, 219, 223, 227, 231, 235]
    assert len(origins) == 9
    for idx in origins:
        assert idx >= 59
        assert idx + 4 <= 239


def test_origin_spec_hard_leakage_boundary(synthetic_periods):
    split = make_split(synthetic_periods)
    window = (split.validation_start, split.validation_end)
    for idx in tuning_origin_indices(204, split):
        spec = build_origin_spec(synthetic_periods, idx, "month", window)
        assert spec.feature_max_period <= spec.origin_period
        assert spec.feature_max_period == synthetic_periods[idx]
        assert len(spec.label_periods) == 4
        assert all(label > spec.origin_period for label in spec.label_periods)
        assert spec.label_periods[0] == period_add(spec.origin_period, "month", 1)
        assert spec.train_row_count == idx + 1
        assert spec.issue_period == spec.origin_period
        assert spec.data_through == spec.origin_period


def test_origin_spec_label_window_counts(synthetic_periods):
    split = make_split(synthetic_periods)
    window = (split.validation_start, split.validation_end)
    # origin 199: labels 200..203 all inside the validation window
    assert build_origin_spec(synthetic_periods, 199, "month", window).validation_row_count == 4
    # origin 155: labels 156..159 all inside the train window -> 0 in validation
    assert build_origin_spec(synthetic_periods, 155, "month", window).validation_row_count == 0
    # origin 165: labels 166..169 straddle the validation start (index 168) -> 2
    assert build_origin_spec(synthetic_periods, 165, "month", window).validation_row_count == 2
