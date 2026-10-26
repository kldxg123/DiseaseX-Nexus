from __future__ import annotations

import json
from datetime import date

from common.db import get_session, init_db
from common.models import ModelExperienceBank, Prediction, PredictionFeedback
from predictor_agent.feedback_cli import main


def _database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'prediction-feedback-cli.db'}"


def _add_prediction(database_url: str) -> int:
    with get_session(database_url) as session:
        prediction = Prediction(
            country="United States",
            admin1=None,
            disease="norovirus",
            target_date=date(2025, 3, 1),
            iso_week="2025-W08",
            horizon_days=7,
            model_name="seasonal_naive",
            model_version="v1",
            predicted_value=10.0,
            lower_bound=8.0,
            upper_bound=12.0,
            uncertainty_method="rolling_residual_std_95pct",
            prediction_interval_json={"level": 0.95},
            metrics_json={},
            provenance_json={"predicted_outbreak_label": False},
            review_status="pending",
        )
        session.add(prediction)
        session.flush()
        return prediction.id


def test_prediction_feedback_cli_submit_outputs_safe_json(tmp_path, capsys) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    prediction_id = _add_prediction(database_url)

    result = main(
        [
            "--database-url",
            database_url,
            "--prediction-id",
            str(prediction_id),
            "--status",
            "failure",
            "--actual-value",
            "14",
            "--actual-outbreak-label",
            "true",
            "--failure-reason",
            "under_prediction",
            "--notes",
            "do not echo this analyst note",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["prediction_id"] == prediction_id
    assert payload["status"] == "failure"
    assert payload["feedback_created"] is True
    assert "analyst note" not in captured.out
    with get_session(database_url) as session:
        assert session.query(PredictionFeedback).count() == 1
        assert session.query(ModelExperienceBank).count() == 1


def test_prediction_feedback_cli_summary_outputs_aggregates(tmp_path, capsys) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    prediction_id = _add_prediction(database_url)
    main(
        [
            "--database-url",
            database_url,
            "--prediction-id",
            str(prediction_id),
            "--status",
            "success",
            "--actual-value",
            "10",
        ]
    )
    capsys.readouterr()

    result = main(["--database-url", database_url, "--summary"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["feedback_count"] == 1
    assert payload["by_status"]["success"] == 1
    assert "actual-value" not in captured.out


def test_prediction_feedback_cli_experiences_outputs_safe_aggregates(tmp_path, capsys) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    prediction_id = _add_prediction(database_url)
    main(
        [
            "--database-url",
            database_url,
            "--prediction-id",
            str(prediction_id),
            "--status",
            "failure",
            "--actual-value",
            "14",
            "--actual-outbreak-label",
            "true",
            "--notes",
            "do not echo this analyst note",
        ]
    )
    capsys.readouterr()

    result = main(["--database-url", database_url, "--experiences"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["experience_count"] == 1
    assert payload["by_failure_type"] == {"missed_outbreak": 1}
    assert "analyst note" not in captured.out
