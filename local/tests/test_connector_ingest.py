from __future__ import annotations

from pathlib import Path

from common.db import get_session, init_db
from common.models import RawDocument, SourceRegistry
from crawler_agent.connector_ingest import (
    build_connector_source_payload,
    ingest_connector_payload,
    parse_connector_fixture,
    parse_connector_payload,
)


FIXTURES = Path("tests/fixtures")


def _database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'connector-ingest.db'}"


def test_parse_connector_payload_uses_registered_default_base_url() -> None:
    payload = (FIXTURES / "who_don.json").read_text(encoding="utf-8")
    documents = parse_connector_payload("who_disease_outbreak_news", payload)

    assert len(documents) == 1
    assert documents[0].connector_name == "who_disease_outbreak_news"
    assert documents[0].title == "Disease outbreak news fixture"


def test_parse_connector_fixture_reads_local_file_only() -> None:
    documents = parse_connector_fixture(
        "cdc_norostat",
        FIXTURES / "cdc_norostat.html",
    )

    assert len(documents) == 1
    assert documents[0].source_name == "CDC NoroSTAT"
    assert "norovirus reporting parser tests" in documents[0].body_text


def test_build_connector_source_payload_is_metadata_only() -> None:
    payload = build_connector_source_payload("ecdc_erviss")

    assert payload["parser_type"] == "ecdc_erviss"
    assert payload["status"] == "pending_review"
    assert payload["metadata_json"]["connector"] == "ecdc_erviss"
    assert payload["metadata_json"]["requires_network_for_fetching"] is True


def test_ingest_connector_payload_writes_raw_documents_to_temp_sqlite(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    payload = (FIXTURES / "ecdc_surveillance_atlas.csv").read_text(encoding="utf-8")

    with get_session(database_url) as session:
        results = ingest_connector_payload(
            session,
            "ecdc_surveillance_atlas",
            payload,
        )
        source_count = session.query(SourceRegistry).count()
        raw_documents = session.query(RawDocument).order_by(RawDocument.id).all()

    assert len(results) == 2
    assert all(result.created for result in results)
    assert source_count == 1
    assert [document.metadata_json["connector"] for document in raw_documents] == [
        "ecdc_surveillance_atlas",
        "ecdc_surveillance_atlas",
    ]


def test_ingest_connector_payload_deduplicates_on_repeat(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    payload = (FIXTURES / "cdc_nors.json").read_text(encoding="utf-8")

    with get_session(database_url) as session:
        first = ingest_connector_payload(session, "cdc_nors", payload)
        second = ingest_connector_payload(session, "cdc_nors", payload)
        raw_document_count = session.query(RawDocument).count()

    assert first[0].created is True
    assert second[0].created is False
    assert raw_document_count == 1
