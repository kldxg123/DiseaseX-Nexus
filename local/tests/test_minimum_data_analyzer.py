from __future__ import annotations

from datetime import date, timedelta

from common.db import get_session, init_db
from common.models import ModelFeature
from predictor_agent.minimum_data_analyzer import (
    MinimumDataAnalysisRequest,
    MinimumDataAnalyzer,
    result_to_dict,
)


def _database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'minimum-data-analyzer.db'}"


def _seed_features(database_url: str, count: int = 40) -> None:
    start = date(2025, 1, 6)
    with get_session(database_url) as session:
        for index in range(count):
            value = float(index + 1)
            session.add(
                ModelFeature(
                    country="United States",
                    admin1=None,
                    disease="norovirus",
                    target_date=start + timedelta(days=7 * index),
                    iso_week=f"2025-W{index + 1:02d}",
                    horizon_days=28,
                    feature_json={
                        "current_week_cases": value,
                        "lag_1w_cases": value,
                        "lag_2w_cases": value,
                        "rolling_4w_mean_cases": value,
                        "media_document_count": value / 2,
                        "official_document_count": 1.0,
                        "data_quality_mean": 1.0,
                        "season_week_sin": 0.0,
                        "season_week_cos": 1.0,
                    },
                    target_value=value,
                    target_outbreak_label=value >= 12,
                    split_type="train",
                )
            )


def test_minimum_data_analyzer_runs_from_sqlite_model_features(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    _seed_features(database_url)

    with get_session(database_url) as session:
        result = MinimumDataAnalyzer(session).analyze(
            MinimumDataAnalysisRequest(
                country="United States",
                disease="norovirus",
                horizon_days=28,
                target_metric="mae",
                min_acceptable_score=0.1,
                candidate_feature_groups=["case_counts", "media_signals", "data_quality", "seasonality"],
                history_windows_weeks=[8, 12, 20],
                min_train_size=4,
            )
        )

    payload = result_to_dict(result)
    assert payload["minimum_feature_set"] == ["case_counts"]
    assert payload["minimum_history_weeks"] in {8, 12, 20}
    assert payload["expected_score"] == 0.0
    assert payload["target_metric"] == "regression.mae"
    assert payload["metric_direction"] == "lower"
    assert payload["evaluations"]
    assert payload["learning_curve"]
    assert payload["ablation_results"]
    assert payload["removable_features"] or payload["non_removable_features"]
    assert "does not guarantee future accuracy" in payload["explanation"]


def test_minimum_data_analyzer_handles_no_data_without_crashing(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        result = MinimumDataAnalyzer(session).analyze(
            MinimumDataAnalysisRequest(
                country="United States",
                disease="norovirus",
                horizon_days=28,
                target_metric="mae",
                min_acceptable_score=1.0,
            )
        )

    assert result.minimum_feature_set == []
    assert result.minimum_history_weeks is None
    assert result.expected_score is None
    assert result.learning_curve == []
    assert "No model_features rows matched" in result.explanation
    assert result.warnings


def test_minimum_data_analyzer_returns_warning_when_threshold_unmet(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    _seed_features(database_url)

    with get_session(database_url) as session:
        result = MinimumDataAnalyzer(session).analyze(
            country="United States",
            disease="norovirus",
            horizon_days=28,
            target_metric="mae",
            min_acceptable_score=-1.0,
            candidate_feature_groups=["case_history", "quality", "seasonality"],
            history_windows_weeks=[8, 12],
            min_train_size=4,
        )

    assert result.minimum_feature_set == []
    assert result.expected_score is not None
    assert "No feature subset met the requested threshold." in result.warnings
