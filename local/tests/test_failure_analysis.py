from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

from predictor_agent.failure_analysis import (
    assign_retraining_weight,
    build_failure_lesson,
    classify_failure_type,
)


def _prediction(*, probability: float = 0.8, predicted_value: float = 10.0):
    return SimpleNamespace(
        id=1,
        country="United States",
        admin1=None,
        disease="norovirus",
        target_date=date(2025, 3, 1),
        iso_week="2025-W08",
        horizon_days=7,
        model_name="feature_rule",
        model_version="baseline:feature_rule:h7:v1",
        predicted_value=predicted_value,
        metrics_json={"predicted_outbreak_probability": probability},
        provenance_json={"feature_snapshot_id": "feature-1"},
    )


def _feedback(**kwargs):
    defaults = {
        "id": 1,
        "prediction_id": 1,
        "verdict": "fail",
        "true_cases": 3.0,
        "true_outbreak_label": False,
        "reason": None,
        "notes": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_classify_failure_type_detects_probability_and_text_patterns() -> None:
    assert classify_failure_type(feedback=_feedback(), prediction=_prediction(probability=0.9)) == "false_alarm"
    assert (
        classify_failure_type(
            feedback=_feedback(true_outbreak_label=True),
            prediction=_prediction(probability=0.1),
        )
        == "late_signal"
    )
    assert (
        classify_failure_type(
            feedback=_feedback(reason="missing data source"),
            prediction=_prediction(probability=0.4),
        )
        == "missing_source"
    )
    assert (
        classify_failure_type(
            feedback=_feedback(reason="parser extracted wrong number"),
            prediction=_prediction(probability=0.4),
        )
        == "extraction_error"
    )
    assert (
        classify_failure_type(
            feedback=_feedback(notes="reported late by local label"),
            prediction=_prediction(probability=0.4),
        )
        == "label_delay"
    )


def test_assign_retraining_weight_range_and_ordering() -> None:
    success = assign_retraining_weight(failure_type="unknown", verdict="success")
    partial = assign_retraining_weight(failure_type="model_error", verdict="partial", absolute_error=2.0)
    failure = assign_retraining_weight(failure_type="false_alarm", verdict="fail", absolute_error=10.0)

    assert 0.0 <= success <= 5.0
    assert 0.0 <= partial <= 5.0
    assert 0.0 <= failure <= 5.0
    assert failure > partial > success


def test_build_failure_lesson_is_safe_and_json_serializable() -> None:
    lesson = build_failure_lesson(
        feedback=_feedback(reason="source gap", notes="do not include full note"),
        prediction=_prediction(probability=0.9),
        failure_type="missing_source",
    )
    encoded = json.dumps(lesson, sort_keys=True)

    assert lesson["lesson_version"] == "feedback_experience_v1"
    assert lesson["failure_type"] == "missing_source"
    assert lesson["weight_for_retraining"] > 0
    assert lesson["auto_retrain_triggered"] is False
    assert "full note" not in encoded
