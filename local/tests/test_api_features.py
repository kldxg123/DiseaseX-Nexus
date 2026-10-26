from __future__ import annotations

from common.db import get_session
from tests.conftest import add_api_features


def test_features_endpoint_returns_feature_keys_not_full_feature_json(api_client, api_database_url) -> None:
    with get_session(api_database_url) as session:
        add_api_features(session, count=3, horizon_days=28)

    response = api_client.get(
        "/features",
        params={"country": "United States", "disease": "norovirus", "horizon_days": 28},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 3
    row = payload["features"][0]
    assert "feature_keys" in row
    assert "current_week_cases" in row["feature_keys"]
    assert "feature_json" not in row
