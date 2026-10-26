from __future__ import annotations

from common.db import get_session
from tests.conftest import add_api_features


def test_minimum_data_api_returns_analysis_payload(api_client, api_database_url) -> None:
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
    assert payload["minimum_feature_set"] == ["case_counts"]
    assert payload["learning_curve"]
    assert payload["ablation_results"]
    assert "guaranteed" not in payload["explanation"].lower()
    assert payload["auto_retrain_triggered"] is False
    assert payload["auto_model_replacement_triggered"] is False
