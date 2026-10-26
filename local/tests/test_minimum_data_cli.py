from __future__ import annotations

import json
from datetime import date, timedelta

from common.db import get_session, init_db
from common.models import ModelFeature
from predictor_agent.minimum_data_cli import main


def _database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'minimum-data-cli.db'}"


def _seed_features(database_url: str, count: int = 18) -> None:
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
                    target_outbreak_label=value >= 10,
                    split_type="train",
                )
            )


def test_minimum_data_cli_analyze_outputs_safe_json(tmp_path, capsys) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    _seed_features(database_url)

    result = main(
        [
            "analyze",
            "--database-url",
            database_url,
            "--country",
            "United States",
            "--disease",
            "norovirus",
            "--horizon-days",
            "28",
            "--target-metric",
            "mae",
            "--min-score",
            "0.1",
            "--feature-groups",
            "case_counts,media_signals,data_quality,seasonality",
            "--history-windows-weeks",
            "8,12",
            "--min-train-size",
            "4",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["minimum_feature_set"] == ["case_counts"]
    assert payload["learning_curve"]
    assert "feature_json" not in captured.out


def test_minimum_data_cli_unknown_feature_group_returns_nonzero(tmp_path, capsys) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    result = main(
        [
            "analyze",
            "--database-url",
            database_url,
            "--horizon-days",
            "28",
            "--target-metric",
            "mae",
            "--min-score",
            "1.0",
            "--feature-groups",
            "not_a_group",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 2
    assert payload["status"] == "error"
    assert "Unknown feature group" in payload["error"]


def test_minimum_data_cli_supports_no_subcommand_and_candidate_feature_groups_alias(tmp_path, capsys) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    _seed_features(database_url, count=40)

    result = main(
        [
            "--database-url",
            database_url,
            "--country",
            "United States",
            "--disease",
            "norovirus",
            "--horizon-days",
            "28",
            "--target-metric",
            "mae",
            "--min-score",
            "0.1",
            "--candidate-feature-groups",
            "case_history,quality,seasonality",
            "--history-windows-weeks",
            "8,12",
            "--min-train-size",
            "4",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["minimum_feature_set"] == ["case_history"]
    assert payload["minimum_history_weeks"] in {8, 12}
    assert payload["expected_score"] == 0.0
    assert payload["confidence_interval"] is not None
    assert "feature_json" not in captured.out
