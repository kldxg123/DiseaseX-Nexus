from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from apps.api.deps import get_db_session
from apps.api.routers.sources import run_gdelt_mock_discovery
from apps.api.schemas import JobResponse, MockCrawlRequest, MockGdeltDiscoveryRequest, OfficialConnectorParseRequest
from common.models import SourceRegistry
from crawler_agent.connector_ingest import ingest_connector_payload, parse_connector_payload
from crawler_agent.content_extraction import extract_content
from crawler_agent.http_fetcher import FetchedPage
from crawler_agent.raw_document_store import RawDocumentStore
from crawler_agent.url_utils import normalize_url

router = APIRouter(tags=["crawl"])


@router.post("/crawl/mock", response_model=JobResponse)
@router.post("/crawl/mock-url", response_model=JobResponse)
def crawl_mock(request: MockCrawlRequest, session: Session = Depends(get_db_session)) -> JobResponse:
    try:
        source_id = int(request.source_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("source_id must identify an existing source.") from exc
    source = session.get(SourceRegistry, source_id)
    if source is None:
        raise ValueError("source_id must identify an existing source.")
    normalized_url = normalize_url(request.url)
    fetched = FetchedPage(
        url=normalized_url,
        final_url=normalized_url,
        status_code=200,
        content_type="text/html; charset=utf-8",
        text=request.html,
    )
    extracted = extract_content(request.html)
    result = RawDocumentStore(session).save(source=source, fetched_page=fetched, extracted_content=extracted)
    result.document.country_detected = request.country_detected
    result.document.disease_detected = request.disease_detected
    result.document.metadata_json = {
        **(result.document.metadata_json or {}),
        "fetcher": "api_mock",
        "language": request.language,
    }
    return JobResponse(
        status="ok",
        summary={
            "saved": result.created,
            "duplicate": not result.created,
            "raw_document_id": result.document.id,
            "source_id": source.id,
            "url": normalized_url,
            "content_hash_prefix": result.document.content_hash[:12],
        },
    )


@router.post("/crawl/discover-gdelt-mock", response_model=JobResponse)
def crawl_discover_gdelt_mock(
    request: MockGdeltDiscoveryRequest,
    session: Session = Depends(get_db_session),
) -> JobResponse:
    return run_gdelt_mock_discovery(request, session)


@router.post("/connectors/official/parse", response_model=JobResponse)
@router.post("/crawl/parse-official-fixture", response_model=JobResponse)
def parse_official_connector(
    request: OfficialConnectorParseRequest,
    session: Session = Depends(get_db_session),
) -> JobResponse:
    parsed = parse_connector_payload(request.connector, request.fixture_content)
    results = ingest_connector_payload(session, request.connector, request.fixture_content)
    saved = sum(1 for result in results if result.created)
    duplicates = len(results) - saved
    source_ids = sorted({result.document.source_registry_id for result in results})
    return JobResponse(
        status="ok",
        summary={
            "parsed": len(parsed),
            "saved": saved,
            "duplicates": duplicates,
            "sources_created_or_updated": len(source_ids),
        },
    )
