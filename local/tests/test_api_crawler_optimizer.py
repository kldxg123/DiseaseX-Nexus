from __future__ import annotations

import json

from common.db import get_session
from common.models import SourceRegistry
from tests.conftest import add_api_raw_document, add_api_source


FORBIDDEN = (
    "raw_text",
    "raw_html",
    "<html>",
    "feature_json",
    "lesson_json",
    "provenance_json",
    "metadata_json",
    "secret",
    "token",
    "password",
    "ssh_key_path",
)


def _seed_optimizer_rows(database_url: str) -> tuple[int, int]:
    with get_session(database_url) as session:
        source = add_api_source(session, name="Optimizer API Source")
        source.consecutive_failures = 6
        source.metadata_json = {"last_failure_reason": "http 404", "source_score": 0.3}
        document = add_api_raw_document(
            session,
            source,
            body_text="short",
        )
        document.extraction_status = "failed"
        candidate = SourceRegistry(
            name="Optimizer Candidate",
            base_url="https://candidate.invalid/",
            country_scope=["United States"],
            disease_scope=["norovirus"],
            source_type="candidate",
            reliability_score=0.9,
            parser_type="metadata_only",
            status="pending_review",
            metadata_json={"source_score": 0.85},
        )
        session.add(candidate)
        session.flush()
        return source.id, candidate.id


def test_crawler_optimizer_analyze_returns_safe_summary(api_client, api_database_url) -> None:
    _seed_optimizer_rows(api_database_url)

    response = api_client.post(
        "/crawler-optimizer/analyze",
        json={"country": "United States", "disease": "norovirus"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["sources_analyzed"] >= 2
    assert payload["documents_analyzed"] >= 1
    assert payload["failure_case_count"] >= 1
    assert payload["recommended_action_count"] >= 1
    assert payload["connector_spec_count"] >= 1
    assert payload["recommended_actions"][0]["requires_human_review"] in {True, False}
    assert "limitations" in payload
    _assert_safe(payload)


def test_crawler_optimizer_analyze_source_and_not_found_are_safe(api_client, api_database_url) -> None:
    source_id, _candidate_id = _seed_optimizer_rows(api_database_url)

    response = api_client.post(
        "/crawler-optimizer/analyze-source",
        json={"source_id": str(source_id), "country": "United States", "disease": "norovirus"},
    )
    missing = api_client.post(
        "/crawler-optimizer/analyze-source",
        json={"source_id": "999999", "country": "United States", "disease": "norovirus"},
    )

    assert response.status_code == 200
    assert response.json()["sources_analyzed"] == 1
    assert missing.status_code == 404
    _assert_safe(response.json())
    _assert_safe(missing.json())


def test_crawler_optimizer_connector_spec_returns_safe_json_and_markdown(api_client, api_database_url) -> None:
    _source_id, candidate_id = _seed_optimizer_rows(api_database_url)

    response = api_client.post(
        "/crawler-optimizer/connector-spec",
        json={"source_id": str(candidate_id), "markdown": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["connector_spec"]["requires_human_review"] is True
    assert payload["connector_spec"]["test_fixture_required"] is True
    assert "markdown" in payload
    _assert_safe(payload)


def _assert_safe(payload: dict) -> None:
    rendered = json.dumps(payload, ensure_ascii=False).lower()
    for marker in FORBIDDEN:
        assert marker.lower() not in rendered
