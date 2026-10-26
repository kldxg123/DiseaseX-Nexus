from __future__ import annotations

import httpx

from common.db import get_session, init_db
from common.models import RawDocument, SourceRegistry
from live_source_search_orchestrator.acquisition_bridge import (
    build_candidate_acquisition_plan,
    candidate_acquisition_result_to_connector_document,
    run_candidate_acquisition,
)


def test_candidate_acquisition_single_mock_request_writes_raw_document(tmp_path):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert request.headers.get("range") == "bytes=0-65535"
        html = """
        <html><head><title>Measles Cases and Outbreaks</title></head>
        <body>
        <h1>Measles Cases and Outbreaks</h1>
        <p>On March 1 2026, United States reported 12 confirmed measles cases and 1 outbreak.</p>
        <table><tr><th>Location</th><th>Cases</th></tr><tr><td>United States</td><td>12</td></tr></table>
        </body></html>
        """
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    plan = build_candidate_acquisition_plan(
        _candidate(),
        tmp_path / "reports",
        metadata_result=_metadata(),
        country="United States",
        disease="measles",
    )
    database_url = f"sqlite:///{tmp_path / 'acquisition.db'}"
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        result = run_candidate_acquisition(
            plan,
            allow_network=True,
            terms_reviewed=True,
            manual_reviewed=True,
            transport=httpx.MockTransport(handler),
            session=session,
        )
        raw_count = session.query(RawDocument).count()
        source = session.query(SourceRegistry).one()

    assert calls == [_candidate()["url"]]
    assert result.status == "ok"
    assert result.request_count == 1
    assert result.network_accessed is True
    assert result.follow_links is False
    assert result.files_downloaded is False
    assert result.full_body_saved is False
    assert result.connector_documents_created >= 1
    assert result.raw_documents_saved >= 1
    assert result.source_registry_status == "pending_review"
    assert raw_count >= 1
    assert source.status == "pending_review"
    assert candidate_acquisition_result_to_connector_document(result) is not None


def _candidate() -> dict:
    return {
        "candidate_id": "abc",
        "title": "Cases and Outbreaks",
        "domain": "www.cdc.gov",
        "url": "https://www.cdc.gov/measles/data-research/index.html",
        "quality_gate_status": "pass",
        "metadata_check_status": "data_source_candidate",
        "data_source_likelihood": 0.68,
        "source_type": "official_report_page",
        "parser_strategy": "html_table",
    }


def _metadata() -> dict:
    return {
        "url": _candidate()["url"],
        "domain": "www.cdc.gov",
        "status": "data_source_candidate",
        "data_source_likelihood": 0.68,
        "parser_strategy_hint": "html_table",
        "detected_source_type": "html_table",
    }
