from __future__ import annotations

from common.db import get_session
from tests.conftest import add_api_features


def test_minimum_data_endpoint_returns_evaluation_preview_not_full_features(api_client, api_database_url) -> None:
    with get_session(api_database_url) as session:
        add_api_features(session, count=18, horizon_days=28)

    response = api_client.post(
        "/analysis/minimum-data",
        json={
            "country": "United States",
            "disease": "norovirus",
            "horizon_days": 28,
            "target_metric": "mae",
            "min_acceptable_score": 0.1,
            "candidate_feature_groups": ["case_counts", "data_quality", "seasonality"],
            "history_windows_weeks": [8, 12],
            "model_name": "feature_rule",
            "min_train_size": 4,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert "evaluation_count" in payload
    assert "evaluation_preview" in payload
    assert "evaluations" not in payload
    assert "feature_json" not in str(payload)
    assert payload["auto_retrain_triggered"] is False
    assert payload["auto_model_replacement_triggered"] is False


def test_model_promotion_check_returns_offline_decision(api_client) -> None:
    response = api_client.post(
        "/analysis/model-promotion-check",
        json={
            "champion": {
                "model_version": "champion-v1",
                "model_name": "moving_average",
                "metrics": {"regression": {"mae": 10.0}, "classification": {"false_alarm_rate": 0.2}},
                "warnings": [],
            },
            "challenger": {
                "model_version": "challenger-v1",
                "model_name": "feature_rule",
                "metrics": {"regression": {"mae": 8.0}, "classification": {"false_alarm_rate": 0.2}},
                "warnings": [],
            },
            "primary_metric": "regression.mae",
            "lower_is_better": True,
            "min_relative_improvement": 0.05,
            "max_false_alarm_regression": 0.05,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["promote"] is True
    assert payload["auto_retrain_triggered"] is False
    assert payload["auto_model_replacement_triggered"] is False
    assert "production" in payload["limitations"].lower()
