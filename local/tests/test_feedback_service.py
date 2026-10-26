from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from common.db import get_session, init_db
from common.models import HumanFeedback, Prediction
from predictor_agent.feedback import FeedbackInput, FeedbackService, normalize_verdict, validate_feedback_input


def _database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'feedback-service.db'}"


def _prediction(session, *, country: str = "United States", disease: str = "norovirus") -> Prediction:
    row = Prediction(
        country=country,
        admin1=None,
        disease=disease,
        target_date=date(2025, 3, 1),
        iso_week="2025-W08",
        horizon_days=7,
        model_name="moving_average",
        model_version="baseline:moving_average:h7:v1",
        predicted_value=10.0,
        lower_bound=8.0,
        upper_bound=12.0,
        uncertainty_method="baseline_interval",
        prediction_interval_json={"level": 0.95},
        metrics_json={"predicted_outbreak_probability": 0.2},
        provenance_json={"feature_snapshot_id": "feature-1"},
        review_status="pending",
    )
    session.add(row)
    session.flush()
    return row


def test_normalize_verdict_accepts_case_and_rejects_invalid() -> None:
    assert normalize_verdict("FAIL") == "fail"
    assert normalize_verdict("partial_success") == "partial"
    with pytest.raises(ValueError):
        normalize_verdict("not-a-verdict")


def test_validate_feedback_input_reports_required_and_numeric_errors() -> None:
    errors = validate_feedback_input(SimpleNamespace(prediction_id="", verdict="bad", true_cases=-1))

    assert "prediction_id is required." in errors
    assert "verdict is invalid." in errors
    assert "true_cases must be non-negative." in errors


def test_feedback_service_submit_summary_and_missing_prediction(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        prediction = _prediction(session)
        service = FeedbackService(session)
        result = service.submit_feedback(
            FeedbackInput(
                prediction_id=str(prediction.id),
                reviewer=None,
                verdict="fail",
                true_cases=15.0,
                true_outbreak_label=True,
                reason="false negative",
                notes="local fixture note",
            )
        )
        rows = session.query(HumanFeedback).all()
        summary = service.summarize_feedback(country="United States", disease="norovirus")

        with pytest.raises(ValueError, match="prediction_id"):
            service.submit_feedback(FeedbackInput(prediction_id="9999", verdict="success"))

    assert result["feedback_created"] is True
    assert result["verdict"] == "fail"
    assert len(rows) == 1
    assert rows[0].reviewer_ref == "anonymous"
    assert rows[0].true_cases == 15.0
    assert summary["total"] == 1
    assert summary["fail"] == 1
    assert summary["failure_rate"] == 1.0
