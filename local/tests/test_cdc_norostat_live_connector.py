from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from crawler_agent import LiveFetchGuard, load_access_policies
from crawler_agent.connectors import CdcNoroStatConnector, ConnectorDocument

FIXTURE = Path("tests/fixtures/cdc_norostat_live_mock.html")
LIVE_URL = "https://www.cdc.gov/norovirus/php/reporting/norostat-data-table.html"


def _transport() -> httpx.MockTransport:
    html = FIXTURE.read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith(LIVE_URL)
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    return httpx.MockTransport(handler)


def test_cdc_norostat_fetch_live_default_denies_network() -> None:
    connector = CdcNoroStatConnector()

    with pytest.raises(PermissionError, match="live network access was not explicitly allowed"):
        connector.fetch_live(url=LIVE_URL)


def test_cdc_norostat_mock_transport_fetch_parses_table_documents() -> None:
    connector = CdcNoroStatConnector()
    guard = LiveFetchGuard(load_access_policies("configs/source_access_policy.example.json"))

    documents = connector.fetch_live(
        url=LIVE_URL,
        allow_network=True,
        access_guard=guard,
        robots_allowed=True,
        terms_reviewed=True,
        manual_reviewed=True,
        request_count=1,
        transport=_transport(),
    )

    assert len(documents) == 3
    assert all(isinstance(document, ConnectorDocument) for document in documents)
    assert documents[0].title == "CDC NoroSTAT norovirus outbreaks - California - 2026-W03"
    assert documents[2].title == "CDC NoroSTAT norovirus outbreaks - Texas - 2026-W05"
    assert documents[0].metadata["parse_mode"] == "html_table"
    assert documents[0].metadata["live_fetch"] is True
    rendered = json.dumps([document.__dict__ for document in documents], ensure_ascii=False).lower()
    assert "<html" not in rendered
    assert "token" not in rendered
    assert "password" not in rendered


def test_cdc_norostat_parse_keeps_plain_fixture_summary_compatibility() -> None:
    connector = CdcNoroStatConnector()
    payload = Path("tests/fixtures/cdc_norostat.html").read_text(encoding="utf-8")

    documents = connector.parse(payload, base_url=LIVE_URL)

    assert len(documents) == 1
    assert documents[0].title == "CDC NoroSTAT Fixture"
    assert documents[0].metadata["parse_mode"] == "html_summary"


def test_cdc_norostat_parse_table_fixture_tolerates_missing_fields() -> None:
    connector = CdcNoroStatConnector()

    documents = connector.parse(FIXTURE.read_text(encoding="utf-8"), base_url=LIVE_URL)

    assert len(documents) == 3
    assert "Texas" in documents[2].title
    assert "Confirmed outbreaks: 1." in documents[2].body_text
