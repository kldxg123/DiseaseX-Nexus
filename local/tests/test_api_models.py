from __future__ import annotations

from common.db import get_session
from common.models import Prediction
from tests.conftest import add_api_features


def test_models_backtest_supports_moving_average(api_client, api_database_url) -> None:
    with get_session(api_database_url) as session:
        add_api_features(session, count=12, horizon_days=28)

    response = api_client.post(
        "/models/backtest",
        json={
            "country": "United States",
            "disease": "norovirus",
            "horizon_days": 28,
            "model_name": "moving_average",
            "min_train_size": 4,
        },
    )

    assert response.status_code == 200
    summary = response.json()["summary"]
    assert summary["features_seen"] == 12
    assert summary["backtest_records"] == 8
    assert len(summary["preview"]) <= 20
    assert "metrics" in summary


def test_models_backtest_supports_feature_rule(api_client, api_database_url) -> None:
    with get_session(api_database_url) as session:
        add_api_features(session, count=12, horizon_days=28)

    response = api_client.post(
        "/models/backtest",
        json={
            "country": "United States",
            "disease": "norovirus",
            "horizon_days": 28,
            "model_name": "feature_rule",
            "min_train_size": 4,
        },
    )

    assert response.status_code == 200
    assert response.json()["summary"]["metrics"]["model_name"] == "feature_rule"


def test_metrics_backtest_endpoint_returns_safe_summary(api_client, api_database_url) -> None:
    with get_session(api_database_url) as session:
        add_api_features(session, count=12, horizon_days=28)

    response = api_client.get(
        "/metrics/backtest",
        params={
            "country": "United States",
            "disease": "norovirus",
            "horizon_days": 28,
            "model_name": "seasonal_naive",
            "min_train_size": 4,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["model_name"] == "seasonal_naive"
    assert payload["backtest_records"] == 8
    assert "limitations" in payload
    assert "feature_json" not in str(payload)


def test_models_predict_writes_predictions_and_list_returns_safe_rows(api_client, api_database_url) -> None:
    with get_session(api_database_url) as session:
        add_api_features(session, count=12, horizon_days=28)

    response = api_client.post(
        "/models/predict",
        json={
            "country": "United States",
            "disease": "norovirus",
            "horizon_days": 28,
            "model_name": "feature_rule",
            "limit": 10,
        },
    )

    assert response.status_code == 200
    assert response.json()["summary"]["prediction_rows"] == 1
    list_response = api_client.get("/predictions?country=United States&disease=norovirus&horizon_days=28")
    payload = list_response.json()
    assert list_response.status_code == 200
    assert payload["count"] == 1
    assert payload["predictions"][0]["model_name"] == "feature_rule"
    assert "current_week_cases" not in str(payload)
    with get_session(api_database_url) as session:
        assert session.query(Prediction).count() == 1


def test_models_predict_can_skip_writing_predictions(api_client, api_database_url) -> None:
    with get_session(api_database_url) as session:
        add_api_features(session, count=12, horizon_days=28)

    response = api_client.post(
        "/models/predict",
        json={
            "country": "United States",
            "disease": "norovirus",
            "horizon_days": 28,
            "model_name": "feature_rule",
            "write_predictions": False,
        },
    )

    assert response.status_code == 200
    assert response.json()["summary"]["prediction_rows"] == 1
    assert response.json()["summary"]["predictions_written"] == 0
    with get_session(api_database_url) as session:
        assert session.query(Prediction).count() == 0
