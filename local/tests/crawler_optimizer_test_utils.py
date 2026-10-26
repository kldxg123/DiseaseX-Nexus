from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from common.db import get_session, init_db
from common.models import RawDocument, SourceRegistry


def database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'crawler-optimizer.db'}"


def init_test_db(tmp_path) -> str:
    url = database_url(tmp_path)
    init_db(url).dispose()
    return url


def add_source(
    session,
    *,
    name: str = "Fixture Source",
    status: str = "active",
    failures: int = 0,
    robots_allowed: bool | None = True,
    metadata: dict | None = None,
    score: float = 0.5,
):
    source = SourceRegistry(
        name=name,
        base_url=f"https://fixture.invalid/{name.lower().replace(' ', '-')}",
        country_scope=["United States", "global"],
        disease_scope=["norovirus", "all"],
        source_type="official_page",
        authority_score=score,
        relevance_score=score,
        freshness_score=score,
        reliability_score=score,
        structured_data_score=score,
        historical_success_score=score,
        language_country_match=score,
        legal_or_robots_penalty=0.0,
        robots_allowed=robots_allowed,
        parser_type="generic_html",
        status=status,
        consecutive_failures=failures,
        last_failed_at=datetime(2025, 1, 1, tzinfo=UTC) if failures else None,
        metadata_json={"source_score": score, **(metadata or {})},
    )
    session.add(source)
    session.flush()
    return source


def add_document(
    session,
    source,
    *,
    title: str | None = "Fixture title",
    body: str = "United States reported a norovirus outbreak with 12 cases.",
    status_code: int | None = 200,
    content_type: str | None = "text/html",
    extraction_status: str = "pending",
    metadata: dict | None = None,
    url_suffix: str = "doc",
):
    digest = hashlib.sha256(f"{title or ''}\n{body}".encode("utf-8")).hexdigest()
    document = RawDocument(
        source_registry_id=source.id,
        original_url=f"https://fixture.invalid/{url_suffix}",
        normalized_url=f"https://fixture.invalid/{url_suffix}",
        title=title,
        body_text=body,
        content_hash=digest,
        status_code=status_code,
        content_type=content_type,
        published_at=datetime(2025, 1, 2, tzinfo=UTC),
        fetched_at=datetime(2025, 1, 2, tzinfo=UTC),
        country_detected="United States",
        disease_detected="norovirus",
        extraction_status=extraction_status,
        metadata_json=metadata or {},
    )
    session.add(document)
    session.flush()
    return document


def populated_optimizer_db(tmp_path) -> str:
    url = init_test_db(tmp_path)
    with get_session(url) as session:
        degraded = add_source(session, name="Degraded Source", status="degraded", failures=6, score=0.3)
        candidate = add_source(session, name="Better Candidate", status="pending_review", failures=0, score=0.85)
        add_document(session, degraded, extraction_status="failed", body="short", url_suffix="failed")
        add_document(session, degraded, body="United States norovirus table rows columns cases.", metadata={"table": True}, url_suffix="table")
        add_document(session, candidate, body="United States norovirus outbreak fixture with enough text for relevance.", url_suffix="candidate")
    return url
