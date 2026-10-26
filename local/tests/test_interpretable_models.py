from __future__ import annotations

from dataclasses import replace

import pytest

from model_benchmark_agent.backtest_runner import generate_synthetic_feature_records
from model_benchmark_agent.interpretable_models import (
    FeatureRuleBaseline,
    LagRollingLinearBaseline,
    PositivityAdjustedBaseline,
    WeightedSignalBaseline,
    get_interpretable_model,
)


def test_interpretable_models_return_safe_predictions():
    records = generate_synthetic_feature_records(
        disease="influenza",
        country="United States",
        horizons_days=[7],
        weeks=14,
    )
    feature_groups = [
        "epidemiological_lag",
        "rolling_statistics",
        "testing",
        "positivity",
        "source_signal",
        "data_quality",
    ]
    model_classes = [
        FeatureRuleBaseline,
        WeightedSignalBaseline,
        LagRollingLinearBaseline,
        PositivityAdjustedBaseline,
    ]
    for model_class in model_classes:
        model = model_class(feature_groups=feature_groups).fit(records[:10])
        prediction = model.predict_one(records[10])
        assert prediction.prediction >= 0
        assert 0 <= prediction.probability_score <= 1
        assert prediction.lower_bound <= prediction.upper_bound
        assert prediction.explanation_summary["used_feature_key_count"] >= 1
        assert "feature_json" not in str(prediction.explanation_summary)


def test_positivity_adjusted_baseline_warns_when_positivity_missing():
    records = generate_synthetic_feature_records(
        disease="influenza",
        country="United States",
        horizons_days=[7],
        weeks=12,
    )
    features = dict(records[10].feature_json)
    features.pop("positivity_rate", None)
    target = replace(records[10], feature_json=features)
    model = PositivityAdjustedBaseline(feature_groups=["epidemiological_lag", "positivity"]).fit(records[:10])
    prediction = model.predict_one(target)
    assert "missing_feature_group:positivity" in prediction.warnings
    assert prediction.prediction >= 0


def test_get_interpretable_model_rejects_unknown_name():
    assert get_interpretable_model("feature-rule-baseline").model_name == "feature_rule_baseline"
    with pytest.raises(ValueError):
        get_interpretable_model("unknown_model")
