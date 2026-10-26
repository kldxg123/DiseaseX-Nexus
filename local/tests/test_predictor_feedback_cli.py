from __future__ import annotations

import json
from datetime import date

from common.db import get_session, init_db
from common.models import ModelExperienceBank, Prediction, HumanFeedback
from predictor_agent.agent import main


def _database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'predictor-feedback-cli.db'}"


def _add_prediction(database_url: str) -> int:
    with get_session(database_url) as session:
        row = Prediction(
            country="United States",
            admin1=None,
            disease="norovirus",
            target_date=date(2025, 3, 1),
            iso_week="2025-W08",
            horizon_days=7,
            model_name="feature_rule",
            model_version="baseline:feature_rule:h7:v1",
            predicted_value=4.0,
            lower_bound=2.0,
            upper_bound=6.0,
            uncertainty_method="baseline_interval",
            prediction_interval_json={"level": 0.95},
            metrics_json={"predicted_outbreak_probability": 0.2},
            provenance_json={"feature_snapshot_id": "feature-1"},
            review_status="pending",
        )
        session.add(row)
        session.flush()
        return row.id


def test_agent_cli_submit_feedback_and_build_experience(tmp_path, capsys) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    prediction_id = _add_prediction(database_url)

    result = main(
        [
            "submit-feedback",
            "--database-url",
            database_url,
            "--prediction-id",
            str(prediction_id),
            "--verdict",
            "fail",
            "--true-cases",
            "42",
            "--true-outbreak-label",
            "true",
            "--reason",
            "false negative",
            "--notes",
            "do not echo full note",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["prediction_id"] == prediction_id
    assert payload["verdict"] == "fail"
    assert payload["feedback_created"] is True
    assert "full note" not in captured.out

    result = main(
        [
            "build-experience",
            "--database-url",
            database_url,
            "--feedback-id",
            str(payload["feedback_id"]),
        ]
    )
    captured = capsys.readouterr()
    experience_payload = json.loads(captured.out)

    with get_session(database_url) as session:
        assert session.query(HumanFeedback).count() == 1
        assert session.query(ModelExperienceBank).count() == 1

    assert result == 0
    assert experience_payload["feedback_id"] == payload["feedback_id"]
    assert experience_payload["failure_type"] in {"missed_outbreak", "model_error", "late_signal"}
    assert experience_payload["weight_for_retraining"] is not None
    assert "lesson_json" not in captured.out


def test_agent_cli_feedback_and_experience_summaries(tmp_path, capsys) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    prediction_id = _add_prediction(database_url)
    main(
        [
            "submit-feedback",
            "--database-url",
            database_url,
            "--prediction-id",
            str(prediction_id),
            "--verdict",
            "fail",
            "--true-cases",
            "42",
            "--true-outbreak-label",
            "true",
        ]
    )
    capsys.readouterr()

    assert main(["summarize-feedback", "--database-url", database_url, "--country", "United States"]) == 0
    feedback_summary = json.loads(capsys.readouterr().out)
    assert feedback_summary["total"] == 1
    assert feedback_summary["fail"] == 1

    assert main(["summarize-experiences", "--database-url", database_url, "--disease", "norovirus"]) == 0
    experience_summary = json.loads(capsys.readouterr().out)
    assert experience_summary["total"] == 1
    assert experience_summary["average_weight"] > 0
