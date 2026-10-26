from __future__ import annotations

import pytest

from predictor_agent.baseline import MovingAverageBaseline as LegacyMovingAverageBaseline
from predictor_agent.baselines import (
    FeatureRuleBaseline,
    FeatureRulePredictor,
    MovingAverageBaseline,
    PredictionOutput,
    SeasonalNaiveBaseline,
    SeasonalNaivePredictor,
    get_baseline_model,
    get_predictor_factory,
)
from predictor_agent.dataset import FeatureRow


def _feature_row(target_value: float, feature_json: dict) -> FeatureRow:
    return FeatureRow(
        id=None,
        country="United States",
        admin1=None,
        disease="norovirus",
        target_date=__import__("datetime").date(2025, 1, 6),
        iso_week="2025-W01",
        horizon_days=7,
        feature_json=feature_json,
        target_value=target_value,
        target_outbreak_label=False,
        split_type="train",
    )


def test_seasonal_naive_uses_lag_when_available_and_fallback_when_needed() -> None:
    seasonal = SeasonalNaivePredictor(seasonal_lag_weeks=4, fallback_window_size=2).fit(
        [10, 20, 30, 40, 50, 60]
    )
    fallback = SeasonalNaivePredictor(seasonal_lag_weeks=4, fallback_window_size=2).fit(
        [10, 20, 30]
    )

    assert seasonal.predict().predicted_value == 30.0
    assert fallback.predict().predicted_value == 25.0


def test_feature_rule_predictor_uses_feature_json_and_returns_interval() -> None:
    rows = [
        _feature_row(
            9.0,
            {
                "current_week_cases": 10,
                "rolling_4w_mean_cases": 8,
                "lag_1w_cases": 6,
                "lag_2w_cases": 4,
            },
        )
    ]

    prediction = FeatureRulePredictor().fit(rows).predict()

    assert prediction.predicted_value == 8.0
    assert prediction.lower_bound <= prediction.predicted_value <= prediction.upper_bound
    assert prediction.training_count == 1


def test_predictor_factory_aliases_and_unknown_name() -> None:
    assert isinstance(get_predictor_factory("moving_average_baseline")(), MovingAverageBaseline)
    assert isinstance(get_predictor_factory("moving_average")(), MovingAverageBaseline)
    assert isinstance(get_predictor_factory("seasonal_naive")(), SeasonalNaivePredictor)
    assert isinstance(get_predictor_factory("feature_rule")(), FeatureRulePredictor)
    with pytest.raises(ValueError, match="Unknown predictor"):
        get_predictor_factory("missing")


def test_legacy_moving_average_baseline_still_imports() -> None:
    assert LegacyMovingAverageBaseline is MovingAverageBaseline
    assert MovingAverageBaseline(window_size=2).fit([1, 3]).predict().predicted_value == 2.0


def test_stable_baseline_factory_and_predict_one_output() -> None:
    rows = [
        _feature_row(2.0, {"current_week_cases": 2}),
        _feature_row(4.0, {"current_week_cases": 4}),
        _feature_row(6.0, {"current_week_cases": 6}),
    ]

    moving_average = get_baseline_model("moving_average", window=2).fit(rows)
    seasonal = get_baseline_model("seasonal_naive", fallback_window=2).fit(rows)
    feature_rule = get_baseline_model("feature_rule", window=2).fit(rows)

    assert isinstance(seasonal, SeasonalNaiveBaseline)
    assert isinstance(feature_rule, FeatureRuleBaseline)
    for model in (moving_average, seasonal, feature_rule):
        output = model.predict_one(rows[-1])
        assert isinstance(output, PredictionOutput)
        assert output.predicted_cases >= 0
        assert 0.0 <= output.predicted_outbreak_probability <= 1.0
        assert output.lower_bound <= output.predicted_cases <= output.upper_bound
