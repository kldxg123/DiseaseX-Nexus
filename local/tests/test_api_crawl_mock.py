from __future__ import annotations

from pathlib import Path

from common.db import get_session
from common.models import RawDocument
from tests.conftest import add_api_source


def test_crawl_mock_writes_raw_document_without_echoing_html(api_client, api_database_url) -> None:
    with get_session(api_database_url) as session:
        source = add_api_source(session)
        source_id = source.id

    html = "<html><title>Fixture</title><body>United States reported 12 norovirus cases.</body></html>"
    response = api_client.post(
        "/crawl/mock",
        json={
            "source_id": str(source_id),
            "url": "https://fixture.invalid/mock",
            "html": html,
            "country_detected": "United States",
            "disease_detected": "norovirus",
            "language": "en",
        },
    )

    assert response.status_code == 200
    payload = response.json()["summary"]
    assert payload["saved"] is True
    assert payload["source_id"] == source_id
    assert "United States reported" not in str(response.json())
    with get_session(api_database_url) as session:
        document = session.query(RawDocument).one()
        assert document.title == "Fixture"
        assert document.country_detected == "United States"


def test_official_connector_parse_writes_raw_documents_without_echoing_fixture(api_client, api_database_url) -> None:
    fixture_content = Path("tests/fixtures/who_don.json").read_text(encoding="utf-8")

    response = api_client.post(
        "/connectors/official/parse",
        json={
            "connector": "who_disease_outbreak_news",
            "fixture_content": fixture_content,
            "country": "United States",
            "disease": "norovirus",
        },
    )

    assert response.status_code == 200
    payload = response.json()["summary"]
    assert payload["parsed"] == 1
    assert payload["saved"] == 1
    assert "Disease outbreak news fixture" not in str(response.json())
    with get_session(api_database_url) as session:
        assert session.query(RawDocument).count() == 1
