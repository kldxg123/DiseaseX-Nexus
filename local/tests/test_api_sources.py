from __future__ import annotations

from common.db import get_session
from common.models import SourceRegistry


def _gdelt_mock_payload() -> dict:
    return {
        "articles": [
            {
                "url": "https://news-a.invalid/report",
                "title": "Norovirus update one",
                "sourceCommonName": "News A",
                "language": "English",
                "sourceCountry": "US",
            },
            {
                "url": "https://www.news-a.invalid/second",
                "title": "Norovirus update two",
                "sourceCommonName": "News A",
                "language": "English",
                "sourceCountry": "US",
            },
        ]
    }


def test_sources_seed_dry_run_does_not_write(api_client, api_database_url) -> None:
    response = api_client.post("/sources/seed", json={"dry_run": True})

    assert response.status_code == 200
    assert response.json()["summary"]["candidate_count"] >= 1
    with get_session(api_database_url) as session:
        assert session.query(SourceRegistry).count() == 0


def test_sources_seed_and_list_and_health(api_client, api_database_url) -> None:
    response = api_client.post("/sources/seed", json={"dry_run": False})

    assert response.status_code == 200
    assert response.json()["summary"]["created"] >= 1
    list_response = api_client.get("/sources")
    health_response = api_client.get("/sources/health")

    assert list_response.status_code == 200
    assert list_response.json()["count"] >= 1
    first_source = list_response.json()["sources"][0]
    assert "source_score" in first_source
    assert "metadata_json" not in first_source
    assert "password" not in str(list_response.json()).lower()
    assert health_response.status_code == 200
    assert health_response.json()["total"] >= 1
    assert "active" in health_response.json()["by_status"]


def test_sources_discover_gdelt_mock_writes_pending_review(api_client, api_database_url) -> None:
    response = api_client.post(
        "/sources/discover/gdelt/mock",
        json={
            "country": "United States",
            "disease": "norovirus",
            "lookback_days": 7,
            "min_source_score": 0.1,
            "mock_response_json": _gdelt_mock_payload(),
        },
    )

    assert response.status_code == 200
    assert response.json()["summary"]["upserted"] == 1
    rendered = str(response.json()).lower()
    assert "norovirus update one" not in rendered
    with get_session(api_database_url) as session:
        source = session.query(SourceRegistry).one()
        assert source.status == "pending_review"
        assert source.base_url == "https://news-a.invalid/"


def test_source_review_requires_terms_and_manual_review_before_activation(api_client, api_database_url) -> None:
    api_client.post(
        "/sources/discover/gdelt/mock",
        json={
            "country": "United States",
            "disease": "norovirus",
            "lookback_days": 7,
            "min_source_score": 0.1,
            "mock_response_json": _gdelt_mock_payload(),
        },
    )
    with get_session(api_database_url) as session:
        source_id = session.query(SourceRegistry).one().id

    response = api_client.post(
        f"/sources/{source_id}/review",
        json={"decision": "activate", "terms_reviewed": False, "manual_reviewed": True},
    )

    assert response.status_code == 400
    with get_session(api_database_url) as session:
        assert session.get(SourceRegistry, source_id).status == "pending_review"


def test_source_review_can_activate_pending_source_after_human_checks(api_client, api_database_url) -> None:
    api_client.post(
        "/sources/discover/gdelt/mock",
        json={
            "country": "United States",
            "disease": "norovirus",
            "lookback_days": 7,
            "min_source_score": 0.1,
            "mock_response_json": _gdelt_mock_payload(),
        },
    )
    with get_session(api_database_url) as session:
        source_id = session.query(SourceRegistry).one().id

    response = api_client.post(
        f"/sources/{source_id}/review",
        json={
            "decision": "activate",
            "terms_reviewed": True,
            "manual_reviewed": True,
            "reviewer": "fixture-reviewer",
            "notes": "approved for local fixture test",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"]["status"] == "active"
    rendered = str(payload).lower()
    assert "approved for local fixture test" not in rendered
    assert "metadata_json" not in rendered
    with get_session(api_database_url) as session:
        source = session.get(SourceRegistry, source_id)
        assert source.status == "active"
        assert source.metadata_json["review_decision"] == "activate"
        assert source.metadata_json["notes_provided"] is True
