from __future__ import annotations

from datetime import date, timedelta

from predictor_agent.backtest import backtest_summary, make_rolling_splits, rolling_origin_backtest, run_backtest
from predictor_agent.dataset import FeatureRow
from predictor_agent.datasets import FeatureRecord


def _feature_rows(count: int = 8) -> list[FeatureRow]:
    start = date(2025, 1, 6)
    return [
        FeatureRow(
            id=index + 1,
            country="United States",
            admin1=None,
            disease="norovirus",
            target_date=start + timedelta(days=7 * index),
            iso_week=f"2025-W{index + 1:02d}",
            horizon_days=7,
            feature_json={"current_week_cases": index + 1},
            target_value=float(index + 2),
            target_outbreak_label=False,
            split_type="train",
        )
        for index in range(count)
    ]


def _feature_records(count: int = 10) -> list[FeatureRecord]:
    start = date(2025, 1, 6)
    return [
        FeatureRecord(
            id=str(index + 1),
            country="United States",
            admin1=None,
            disease="norovirus",
            target_date=start + timedelta(days=7 * index),
            iso_week=f"2025-W{index + 1:02d}",
            horizon_days=7,
            feature_json={
                "current_week_cases": index + 1,
                "lag_1w_cases": index,
                "rolling_4w_mean_cases": max(1.0, float(index)),
            },
            target_value=float(index + 2),
            target_outbreak_label=index >= 6,
            split_type="train",
        )
        for index in range(count)
    ]


def test_rolling_origin_backtest_uses_only_prior_rows() -> None:
    records = rolling_origin_backtest(_feature_rows(), min_train_size=4, window_size=2)

    assert len(records) == 4
    assert records[0].iso_week == "2025-W05"
    assert records[0].training_count == 4
    assert records[0].predicted_value == 4.5
    assert records[0].actual_value == 6.0


def test_backtest_summary_contains_basic_metrics() -> None:
    records = rolling_origin_backtest(_feature_rows(), min_train_size=4, window_size=2)
    summary = backtest_summary(records)

    assert summary["model_name"] == "moving_average_baseline"
    assert summary["overall"]["n"] == 4
    assert summary["overall"]["mae"] >= 0
    assert "7" in summary["by_horizon_days"]


def test_rolling_origin_backtest_supports_seasonal_naive() -> None:
    records = rolling_origin_backtest(
        _feature_rows(),
        model_name="seasonal_naive",
        min_train_size=4,
        seasonal_lag_weeks=2,
        fallback_window_size=2,
    )
    summary = backtest_summary(records, model_name="seasonal_naive")

    assert len(records) == 4
    assert records[0].model_name == "seasonal_naive"
    assert records[0].predicted_value == 4.0
    assert summary["model_name"] == "seasonal_naive"


def test_rolling_origin_backtest_supports_feature_rule() -> None:
    records = rolling_origin_backtest(
        _feature_rows(),
        model_name="feature_rule",
        min_train_size=4,
        window_size=2,
    )
    summary = backtest_summary(records, model_name="feature_rule")

    assert len(records) == 4
    assert records[0].model_name == "feature_rule"
    assert records[0].predicted_value >= 0
    assert records[0].lower_bound <= records[0].predicted_value <= records[0].upper_bound
    assert summary["model_name"] == "feature_rule"


def test_make_rolling_splits_uses_prior_records_only() -> None:
    splits = make_rolling_splits(_feature_records(), min_train_size=4, test_window=2)

    assert len(splits) == 3
    for train, test in splits:
        assert train[-1].target_date < test[0].target_date


def test_run_backtest_returns_metrics_predictions_and_warnings() -> None:
    result = run_backtest(
        _feature_records(),
        model_name="feature_rule",
        horizon_days=7,
        min_train_size=4,
        test_window=2,
    )
    insufficient = run_backtest(_feature_records(3), horizon_days=7, min_train_size=4)

    assert result["model_name"] == "feature_rule"
    assert result["splits"] == 3
    assert len(result["predictions"]) == 6
    assert result["metrics"]["regression"]["n"] == 6
    assert result["metrics"]["classification"]["n"] == 6
    assert result["predictions"][0]["predicted_cases"] >= 0
    assert insufficient["warnings"] == ["insufficient records for rolling backtest"]
