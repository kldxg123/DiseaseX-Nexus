from __future__ import annotations

from pathlib import Path

from common.db import get_session
from common.models import RawDocument, SourceRegistry
from tests.conftest import add_api_source


def _gdelt_mock_payload() -> dict:
    return {
        "articles": [
            {
                "url": "https://local-news.invalid/report-a",
                "title": "Fixture norovirus signal",
                "sourceCommonName": "Local News",
                "language": "English",
                "sourceCountry": "US",
            },
            {
                "url": "https://www.local-news.invalid/report-b",
                "title": "Second fixture norovirus signal",
                "sourceCommonName": "Local News",
                "language": "English",
                "sourceCountry": "US",
            },
        ]
    }


def test_crawl_mock_url_alias_writes_raw_document_without_echoing_html(api_client, api_database_url) -> None:
    with get_session(api_database_url) as session:
        source = add_api_source(session)
        source_id = source.id

    html = "<html><title>Alias</title><body>local fixture with secret token text</body></html>"
    response = api_client.post(
        "/crawl/mock-url",
        json={
            "source_id": str(source_id),
            "url": "https://fixture.invalid/alias",
            "html": html,
            "country_detected": "United States",
            "disease_detected": "norovirus",
        },
    )

    assert response.status_code == 200
    rendered = str(response.json()).lower()
    assert "secret token" not in rendered
    assert "<html>" not in rendered
    assert response.json()["summary"]["content_hash_prefix"]
    with get_session(api_database_url) as session:
        assert session.query(RawDocument).count() == 1


def test_crawl_discover_gdelt_mock_alias_writes_pending_review(api_client, api_database_url) -> None:
    response = api_client.post(
        "/crawl/discover-gdelt-mock",
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
    assert "Fixture norovirus signal" not in str(response.json())
    with get_session(api_database_url) as session:
        source = session.query(SourceRegistry).one()
        assert source.status == "pending_review"


def test_crawl_parse_official_fixture_alias_writes_without_echoing_fixture(api_client, api_database_url) -> None:
    fixture_content = Path("tests/fixtures/who_don.json").read_text(encoding="utf-8")

    response = api_client.post(
        "/crawl/parse-official-fixture",
        json={
            "connector": "who_disease_outbreak_news",
            "fixture_content": fixture_content,
            "country": "United States",
            "disease": "norovirus",
        },
    )

    assert response.status_code == 200
    assert response.json()["summary"]["parsed"] == 1
    assert "Disease outbreak news fixture" not in str(response.json())
    with get_session(api_database_url) as session:
        assert session.query(RawDocument).count() == 1
