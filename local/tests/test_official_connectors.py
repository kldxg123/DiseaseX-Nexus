from __future__ import annotations

from pathlib import Path

from common.db import get_session, init_db
from common.models import RawDocument, SourceRegistry
from crawler_agent.official_connectors import (
    CONNECTORS,
    get_official_connector,
    store_connector_documents,
)
from crawler_agent.raw_document_store import RawDocumentStore
from crawler_agent.source_registry import SourceRegistryService


FIXTURES = Path("tests/fixtures")


def _database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'official-connectors.db'}"


def _source_payload(name: str, base_url: str, connector: str) -> dict:
    return {
        "name": name,
        "base_url": base_url,
        "country_scope": ["global"],
        "disease_scope": ["all"],
        "source_type": "official_page",
        "authority_score": 0.9,
        "relevance_score": 0.8,
        "freshness_score": 0.7,
        "reliability_score": 0.9,
        "structured_data_score": 0.6,
        "historical_success_score": 0.6,
        "language_country_match": 0.8,
        "legal_or_robots_penalty": 0.0,
        "robots_allowed": True,
        "parser_type": connector,
        "metadata_json": {"connector": connector},
        "status": "active",
    }


def test_all_official_connectors_are_registered() -> None:
    assert {
        "who_disease_outbreak_news",
        "ecdc_surveillance_atlas",
        "ecdc_erviss",
        "cdc_norostat",
        "cdc_nors",
        "cdc_fluview",
    }.issubset(CONNECTORS)


def test_who_don_fixture_parses_to_connector_document() -> None:
    connector = get_official_connector("who_disease_outbreak_news")
    documents = connector.parse(
        (FIXTURES / "who_don.json").read_text(encoding="utf-8"),
        base_url="https://www.who.int/emergencies/disease-outbreak-news",
    )

    assert len(documents) == 1
    assert documents[0].title == "Disease outbreak news fixture"
    assert "WHO fixture summary" in documents[0].body_text


def test_ecdc_surveillance_atlas_csv_fixture_parses_records() -> None:
    connector = get_official_connector("ecdc_surveillance_atlas")
    documents = connector.parse(
        (FIXTURES / "ecdc_surveillance_atlas.csv").read_text(encoding="utf-8"),
        base_url="https://atlas.ecdc.europa.eu/public/index.aspx",
    )

    assert len(documents) == 2
    assert documents[0].title == "Norovirus EU/EEA 2026"
    assert "cases" in documents[0].body_text


def test_ecdc_erviss_fixture_parses_records() -> None:
    connector = get_official_connector("ecdc_erviss")
    documents = connector.parse(
        (FIXTURES / "ecdc_erviss.json").read_text(encoding="utf-8"),
        base_url="https://erviss.org",
    )

    assert len(documents) == 1
    assert "RSV" in documents[0].title
    assert "increasing" in documents[0].body_text


def test_cdc_norostat_html_fixture_parses_page() -> None:
    connector = get_official_connector("cdc_norostat")
    documents = connector.parse(
        (FIXTURES / "cdc_norostat.html").read_text(encoding="utf-8"),
        base_url="https://www.cdc.gov/norovirus/php/reporting/norostat.html",
    )

    assert len(documents) == 1
    assert documents[0].title == "CDC NoroSTAT Fixture"
    assert "norovirus reporting parser tests" in documents[0].body_text


def test_cdc_nors_fixture_parses_records() -> None:
    connector = get_official_connector("cdc_nors")
    documents = connector.parse(
        (FIXTURES / "cdc_nors.json").read_text(encoding="utf-8"),
        base_url="https://www.cdc.gov/nors/index.html",
    )

    assert len(documents) == 1
    assert "Fixture State" in documents[0].title
    assert "Norovirus" in documents[0].body_text


def test_cdc_fluview_csv_and_html_fixtures_parse_records() -> None:
    connector = get_official_connector("cdc_fluview")
    csv_documents = connector.parse(
        (FIXTURES / "cdc_fluview_ili.csv").read_text(encoding="utf-8"),
        base_url="https://www.cdc.gov/flu/weekly/fluviewinteractive.htm",
    )
    table_documents = connector.parse(
        (FIXTURES / "cdc_fluview_table.html").read_text(encoding="utf-8"),
        base_url="https://www.cdc.gov/flu/weekly/fluview-table-fixture.html",
    )
    report_documents = connector.parse(
        (FIXTURES / "cdc_fluview_report.html").read_text(encoding="utf-8"),
        base_url="https://www.cdc.gov/flu/weekly/index.htm",
    )

    assert len(csv_documents) == 4
    assert len(table_documents) == 2
    assert len(report_documents) == 1
    assert csv_documents[0].metadata["source_key"] == "cdc_fluview"
    assert csv_documents[0].metadata["structured_event"]["disease"] == "influenza"
    assert table_documents[0].metadata["parse_mode"] == "html_table"
    assert report_documents[0].metadata["parse_mode"] == "official_report_page"


def test_connector_documents_can_be_stored_as_raw_documents(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    connector = get_official_connector("cdc_norostat")
    documents = connector.parse(
        (FIXTURES / "cdc_norostat.html").read_text(encoding="utf-8"),
        base_url="https://www.cdc.gov/norovirus/php/reporting/norostat.html",
    )

    with get_session(database_url) as session:
        source = SourceRegistryService(session).add_source(
            **_source_payload(
                "CDC NoroSTAT",
                "https://www.cdc.gov/norovirus/php/reporting/norostat.html",
                "cdc_norostat",
            )
        )
        results = store_connector_documents(
            source=source,
            documents=documents,
            store=RawDocumentStore(session),
        )
        raw_documents = session.query(RawDocument).all()

    assert len(results) == 1
    assert results[0].created is True
    assert len(raw_documents) == 1
    assert raw_documents[0].metadata_json["connector"] == "cdc_norostat"
    assert raw_documents[0].title == "CDC NoroSTAT Fixture"


def test_connector_storage_deduplicates_documents(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    connector = get_official_connector("who_disease_outbreak_news")
    documents = connector.parse(
        (FIXTURES / "who_don.json").read_text(encoding="utf-8"),
        base_url="https://www.who.int/emergencies/disease-outbreak-news",
    )

    with get_session(database_url) as session:
        source = SourceRegistryService(session).add_source(
            **_source_payload(
                "WHO Disease Outbreak News",
                "https://www.who.int/emergencies/disease-outbreak-news",
                "who_disease_outbreak_news",
            )
        )
        first = store_connector_documents(
            source=source,
            documents=documents,
            store=RawDocumentStore(session),
        )
        second = store_connector_documents(
            source=source,
            documents=documents,
            store=RawDocumentStore(session),
        )

    assert first[0].created is True
    assert second[0].created is False
