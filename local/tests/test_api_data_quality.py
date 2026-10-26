from __future__ import annotations

import json

from common.db import get_session
from tests.conftest import add_api_events, add_api_features, add_api_prediction


def test_api_data_quality_profile_label_audit_and_report_are_safe(api_client, api_database_url, tmp_path) -> None:
    with get_session(api_database_url) as session:
        add_api_events(session, count=3)
        add_api_features(session, count=8, horizon_days=28)
        add_api_prediction(session)

    profile_response = api_client.get("/data-quality/profile")
    audit_response = api_client.get(
        "/data-quality/label-audit",
        params={"country": "United States", "disease": "norovirus"},
    )
    report_response = api_client.post(
        "/data-quality/report",
        json={
            "country": "United States",
            "disease": "norovirus",
            "write_files": True,
            "output_dir": str(tmp_path / "reports"),
        },
    )

    for response in (profile_response, audit_response, report_response):
        assert response.status_code == 200
        rendered = json.dumps(response.json(), ensure_ascii=False).lower()
        assert "feature_json" not in rendered
        assert "lesson_json" not in rendered
        assert "<html>" not in rendered
        assert "password" not in rendered
        assert "token" not in rendered

    assert profile_response.json()["profile"]["model_features"]["row_count"] == 8
    assert audit_response.json()["label_audit"]["checked_features"] == 8
    assert report_response.json()["write_files"] is True
    assert report_response.json()["files"]["report_json"].endswith(".json")
