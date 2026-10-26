from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from common.db import get_session
from crawler_optimizer_agent.failure_analysis import (
    analyze_duplicate_documents,
    classify_raw_document_failure,
    classify_source_failure,
    summarize_failure_cases,
)
from tests.crawler_optimizer_test_utils import add_document, add_source, init_test_db


def test_source_failure_thresholds_and_robots(tmp_path) -> None:
    database_url = init_test_db(tmp_path)
    with get_session(database_url) as session:
        degraded = add_source(session, failures=5)
        critical = add_source(session, name="Critical Source", failures=15)
        denied = add_source(session, name="Denied Source", robots_allowed=False)

        degraded_cases = classify_source_failure(degraded)
        critical_cases = classify_source_failure(critical)
        denied_cases = classify_source_failure(denied)

    assert any(case.failure_type == "stale_source" for case in degraded_cases)
    assert any(case.severity in {"high", "critical"} for case in critical_cases)
    assert any(case.failure_type == "robots_denied" for case in denied_cases)


def test_raw_document_failures_and_relevance(tmp_path) -> None:
    database_url = init_test_db(tmp_path)
    with get_session(database_url) as session:
        source = add_source(session)
        empty = add_document(session, source, body="", title=None)
        failed = add_document(session, source, body="tiny", extraction_status="failed", url_suffix="failed")
        missing = add_document(session, source, status_code=404, url_suffix="missing")
        irrelevant = add_document(session, source, body="Weather forecast only.", url_suffix="irrelevant")

        cases = []
        for document in [empty, failed, missing, irrelevant]:
            cases.extend(
                classify_raw_document_failure(
                    document,
                    target_country="United States",
                    target_disease="norovirus",
                )
            )

    types = {case.failure_type for case in cases}
    assert "empty_content" in types
    assert "parser_failed" in types
    assert "broken_link" in types
    assert "low_relevance" in types
    assert all("body_text" not in case.to_dict()["evidence"] for case in cases)


def test_duplicate_documents_are_reported(tmp_path) -> None:
    @dataclass
    class LocalDocument:
        id: int
        source_registry_id: int
        normalized_url: str
        original_url: str
        content_hash: str
        fetched_at: datetime
        source_registry: object | None = None

    first = LocalDocument(
        id=1,
        source_registry_id=1,
        normalized_url="https://fixture.invalid/a",
        original_url="https://fixture.invalid/a",
        content_hash="same-hash",
        fetched_at=datetime.now(UTC),
    )
    second = LocalDocument(
        id=2,
        source_registry_id=1,
        normalized_url="https://fixture.invalid/b",
        original_url="https://fixture.invalid/b",
        content_hash="same-hash",
        fetched_at=datetime.now(UTC),
    )
    cases = analyze_duplicate_documents([first, second])

    assert len(cases) == 2
    assert {case.failure_type for case in cases} == {"duplicate_content"}
    assert summarize_failure_cases(cases)["by_type"] == {"duplicate_content": 2}
