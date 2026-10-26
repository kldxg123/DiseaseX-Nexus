from __future__ import annotations

from common.db import get_session
from common.models import EpidemicEvent, ModelFeature
from tests.conftest import add_api_events, add_api_raw_document, add_api_source


def test_clean_run_creates_events_and_events_list_hides_raw_text(api_client, api_database_url) -> None:
    with get_session(api_database_url) as session:
        source = add_api_source(session)
        add_api_raw_document(session, source)

    response = api_client.post("/clean/run", json={"limit": 10, "country": "United States", "disease": "norovirus"})

    assert response.status_code == 200
    assert response.json()["summary"]["processed"] == 1
    assert response.json()["summary"]["events_created"] == 1
    events_response = api_client.get("/events?country=United States&disease=norovirus")
    payload = events_response.json()
    assert events_response.status_code == 200
    assert payload["count"] == 1
    assert "body_text" not in str(payload)
    assert "raw_text" not in str(payload)
    with get_session(api_database_url) as session:
        assert session.query(EpidemicEvent).count() == 1


def test_features_build_and_list_feature_rows(api_client, api_database_url) -> None:
    with get_session(api_database_url) as session:
        add_api_events(session, count=10)

    response = api_client.post(
        "/features/build",
        json={
            "country": "United States",
            "disease": "norovirus",
            "horizons_days": [7, 28],
            "outbreak_threshold": 3,
        },
    )

    assert response.status_code == 200
    assert response.json()["summary"]["feature_rows"] > 0
    list_response = api_client.get("/features?country=United States&disease=norovirus&horizon_days=28")
    payload = list_response.json()
    assert list_response.status_code == 200
    assert payload["count"] > 0
    assert "body_text" not in str(payload)
    with get_session(api_database_url) as session:
        assert session.query(ModelFeature).count() > 0
