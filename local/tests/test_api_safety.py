from __future__ import annotations

import json

from common.db import get_session
from tests.conftest import add_api_features, add_api_prediction, add_api_source


FORBIDDEN_RESPONSE_SNIPPETS = (
    "服务器信息.txt",
    "secret token",
    "password",
    "ssh_key_path",
    "private key",
    "<html>",
    "feature_json",
    "lesson_json",
)


def test_api_responses_do_not_echo_sensitive_or_full_payload_fields(api_client, api_database_url) -> None:
    with get_session(api_database_url) as session:
        source = add_api_source(session)
        source_id = source.id
        add_api_features(session, count=4, horizon_days=28)
        prediction = add_api_prediction(session)
        prediction_id = prediction.id

    crawl_response = api_client.post(
        "/crawl/mock-url",
        json={
            "source_id": str(source_id),
            "url": "https://fixture.invalid/safe",
            "html": "<html><body>secret token should stay out of output</body></html>",
        },
    )
    features_response = api_client.get("/features", params={"country": "United States", "disease": "norovirus"})
    predictions_response = api_client.get("/predictions", params={"country": "United States", "disease": "norovirus"})
    feedback_response = api_client.post(
        "/feedback",
        json={
            "prediction_id": str(prediction_id),
            "verdict": "failure",
            "true_cases": 12,
            "true_outbreak_label": True,
            "reason": "under_prediction",
            "notes": "secret token analyst detail should not be echoed",
        },
    )
    experiences_response = api_client.get("/experiences")

    for response in (crawl_response, features_response, predictions_response, feedback_response, experiences_response):
        assert response.status_code == 200
        rendered = json.dumps(response.json(), ensure_ascii=False).lower()
        for snippet in FORBIDDEN_RESPONSE_SNIPPETS:
            assert snippet.lower() not in rendered
