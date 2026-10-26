from __future__ import annotations

import json
from pathlib import Path

from common.db import get_session, init_db
from common.models import RawDocument, SourceRegistry
from crawler_agent.connector_ingest import (
    build_connector_source_payload,
    store_connector_documents,
    upsert_connector_source,
)
from crawler_agent.connectors import CdcNoroStatConnector
from crawler_agent.raw_document_store import RawDocumentStore

LIVE_URL = "https://www.cdc.gov/norovirus/php/reporting/norostat-data-table.html"


def _database_url(tmp_path) -> str:
    return f"sqlite:///{(tmp_path / 'cdc-norostat-live.db').as_posix()}"


def test_cdc_norostat_source_payload_marks_live_fetch_supported() -> None:
    payload = build_connector_source_payload("cdc_norostat", base_url=LIVE_URL)

    assert payload["metadata_json"]["connector"] == "cdc_norostat"
    assert payload["metadata_json"]["source_key"] == "cdc_norostat"
    assert payload["metadata_json"]["live_fetch_supported"] is True


def test_cdc_norostat_live_documents_ingest_and_deduplicate(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    documents = CdcNoroStatConnector().parse(
        Path("tests/fixtures/cdc_norostat_live_mock.html").read_text(encoding="utf-8"),
        base_url=LIVE_URL,
    )

    with get_session(database_url) as session:
        source = upsert_connector_source(session, "cdc_norostat", base_url=LIVE_URL, status="pending_review")
        first = store_connector_documents(source=source, documents=documents, store=RawDocumentStore(session))
        second = store_connector_documents(source=source, documents=documents, store=RawDocumentStore(session))
        raw_documents = session.query(RawDocument).order_by(RawDocument.id).all()
        source_row = session.query(SourceRegistry).one()

    assert len(first) == 3
    assert all(result.created for result in first)
    assert all(result.created is False for result in second)
    assert len(raw_documents) == 3
    assert source_row.metadata_json["live_fetch_supported"] is True
    rendered = json.dumps([document.metadata_json for document in raw_documents], ensure_ascii=False).lower()
    assert "<html" not in rendered
    assert "token" not in rendered
    assert "password" not in rendered
