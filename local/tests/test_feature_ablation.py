from __future__ import annotations

from model_benchmark_agent.feature_ablation import (
    M2_M2_FEATURE_SETS,
    evaluate_feature_subset,
    load_feature_ablation_records,
    run_feature_ablation,
)
from model_benchmark_agent.types import BenchmarkRunConfig


def test_feature_ablation_runs_interpretable_baselines():
    config = BenchmarkRunConfig(
        diseases=["influenza"],
        horizons_days=[7],
        train_window_weeks=4,
        rolling_folds=2,
        synthetic_smoke_if_needed=True,
    )
    payload = run_feature_ablation(synthetic_smoke_if_needed=True, config=config)
    rows = payload["ablation_results"]
    assert payload["schema_version"] == "m2_m2_feature_ablation_v1"
    assert rows
    assert {row["model_name"] for row in rows} >= {
        "feature_rule_baseline",
        "weighted_signal_baseline",
        "lag_rolling_linear_baseline",
        "positivity_adjusted_baseline",
    }
    assert any(row["status"] == "executed" for row in rows)
    executed = next(row for row in rows if row["status"] == "executed")
    assert "regression" in executed["metrics"]
    assert executed["test_rows"] >= 1


def test_evaluate_feature_subset_handles_missing_groups_without_crashing():
    records = load_feature_ablation_records(disease="influenza", horizon_days=7)
    row = evaluate_feature_subset(
        records,
        disease="influenza",
        country="United States",
        horizon_days=7,
        model_name="weighted_signal_baseline",
        feature_set_name="weather_only",
        feature_groups=["weather"],
        min_train_size=4,
        rolling_folds=2,
    )
    assert row["status"] == "skipped_missing_features"
    assert "all_selected_feature_groups_missing" in row["warnings"]
    assert "weather" in M2_M2_FEATURE_SETS.get("all_m1_available", []) or row["feature_groups"] == ["weather"]
