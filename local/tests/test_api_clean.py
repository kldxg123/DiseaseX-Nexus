from __future__ import annotations

from tests.conftest import add_api_events
from common.db import get_session


def test_events_endpoint_returns_safe_provenance_summary(api_client, api_database_url) -> None:
    with get_session(api_database_url) as session:
        add_api_events(session, count=2)

    response = api_client.get("/events", params={"country": "United States", "disease": "norovirus"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    row = payload["events"][0]
    assert "provenance_keys" in row
    assert "provenance_json" not in row
    assert "body_text" not in str(payload)
    assert "raw_text" not in str(payload)
