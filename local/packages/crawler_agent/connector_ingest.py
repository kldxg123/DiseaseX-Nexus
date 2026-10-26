from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from common.models import SourceRegistry
from crawler_agent.connectors import ConnectorDocument, get_connector_spec, get_official_connector
from crawler_agent.content_extraction import ExtractedContent
from crawler_agent.http_fetcher import FetchedPage
from crawler_agent.raw_document_store import RawDocumentStore, StoredDocumentResult
from crawler_agent.source_registry import SourceRegistryService


def parse_connector_payload(
    connector_name: str,
    payload: str,
    *,
    base_url: str | None = None,
) -> list[ConnectorDocument]:
    spec = get_connector_spec(connector_name)
    return spec.connector.parse(payload, base_url=base_url or spec.base_url)


def parse_connector_fixture(
    connector_name: str,
    fixture_path: str | Path,
    *,
    base_url: str | None = None,
) -> list[ConnectorDocument]:
    payload = Path(fixture_path).read_text(encoding="utf-8")
    return parse_connector_payload(connector_name, payload, base_url=base_url)


def build_connector_source_payload(
    connector_name: str,
    *,
    base_url: str | None = None,
    status: str = "pending_review",
) -> dict[str, Any]:
    spec = get_connector_spec(connector_name)
    resolved_base_url = base_url or spec.base_url
    return {
        "name": spec.display_name,
        "base_url": resolved_base_url,
        "country_scope": ["global"],
        "disease_scope": ["all"],
        "source_type": spec.source_type,
        "authority_score": 0.9,
        "relevance_score": 0.8,
        "freshness_score": 0.6,
        "reliability_score": 0.9,
        "structured_data_score": 0.7 if spec.source_type == "official_dataset" else 0.5,
        "historical_success_score": 0.5,
        "language_country_match": 0.7,
        "legal_or_robots_penalty": 0.0,
        "robots_allowed": None,
        "parser_type": connector_name,
        "status": status,
        "metadata_json": {
            "connector": connector_name,
            "source_key": connector_name,
            "live_fetch_supported": connector_name == "cdc_norostat",
            "live_readiness": "controlled_live_fetch_required" if connector_name == "cdc_fluview" else "fixture_parser_ready",
            "allowed_use": "metadata and local fixture parsing only",
            "requires_network_for_fetching": True,
        },
    }


def upsert_connector_source(
    session: Session,
    connector_name: str,
    *,
    base_url: str | None = None,
    status: str = "pending_review",
) -> SourceRegistry:
    payload = build_connector_source_payload(
        connector_name,
        base_url=base_url,
        status=status,
    )
    return SourceRegistryService(session).upsert_source(**payload)


def store_connector_documents(
    *,
    source: SourceRegistry,
    documents: list[ConnectorDocument],
    store: RawDocumentStore,
) -> list[StoredDocumentResult]:
    results: list[StoredDocumentResult] = []
    for document in documents:
        fetched_page = FetchedPage(
            url=document.url,
            final_url=document.url,
            status_code=200,
            content_type=document.content_type,
            text=document.body_text,
        )
        extracted = ExtractedContent(title=document.title, body_text=document.body_text)
        result = store.save(
            source=source,
            fetched_page=fetched_page,
            extracted_content=extracted,
        )
        result.document.metadata_json = {
            **(result.document.metadata_json or {}),
            "connector": document.connector_name,
            "source_name": document.source_name,
            **document.metadata,
        }
        country_detected = document.metadata.get("country_detected")
        disease_detected = document.metadata.get("disease_detected")
        if country_detected:
            result.document.country_detected = str(country_detected)
        if disease_detected:
            result.document.disease_detected = str(disease_detected).lower()
        results.append(result)
    return results


def ingest_connector_payload(
    session: Session,
    connector_name: str,
    payload: str,
    *,
    source: SourceRegistry | None = None,
    base_url: str | None = None,
    source_status: str = "pending_review",
) -> list[StoredDocumentResult]:
    spec = get_connector_spec(connector_name)
    resolved_base_url = base_url or spec.base_url
    resolved_source = source or upsert_connector_source(
        session,
        connector_name,
        base_url=resolved_base_url,
        status=source_status,
    )
    documents = get_official_connector(connector_name).parse(payload, base_url=resolved_base_url)
    return store_connector_documents(
        source=resolved_source,
        documents=documents,
        store=RawDocumentStore(session),
    )


def ingest_connector_fixture(
    session: Session,
    connector_name: str,
    fixture_path: str | Path,
    *,
    source: SourceRegistry | None = None,
    base_url: str | None = None,
    source_status: str = "pending_review",
) -> list[StoredDocumentResult]:
    payload = Path(fixture_path).read_text(encoding="utf-8")
    return ingest_connector_payload(
        session,
        connector_name,
        payload,
        source=source,
        base_url=base_url,
        source_status=source_status,
    )
