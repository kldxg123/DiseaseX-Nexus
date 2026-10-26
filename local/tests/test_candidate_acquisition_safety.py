from __future__ import annotations

import json

import httpx

from live_source_search_orchestrator.acquisition_bridge import (
    build_candidate_acquisition_plan,
    run_candidate_acquisition,
)


def test_acquisition_denied_without_required_review_flags(tmp_path):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text="should not be requested")

    plan = build_candidate_acquisition_plan(_candidate(), tmp_path, metadata_result=_metadata())

    result = run_candidate_acquisition(
        plan,
        allow_network=False,
        terms_reviewed=True,
        manual_reviewed=True,
        transport=httpx.MockTransport(handler),
    )

    assert result.status == "denied"
    assert result.request_count == 0
    assert result.network_accessed is False
    assert calls == []


def test_file_like_content_is_not_downloaded_or_saved(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF-sample")

    plan = build_candidate_acquisition_plan(_candidate(), tmp_path, metadata_result=_metadata())

    result = run_candidate_acquisition(
        plan,
        allow_network=True,
        terms_reviewed=True,
        manual_reviewed=True,
        transport=httpx.MockTransport(handler),
    )

    assert result.status == "file_download_candidate"
    assert result.request_count == 1
    assert result.files_downloaded is False
    assert result.raw_documents_saved == 0


def test_report_files_do_not_contain_raw_payload_or_sensitive_terms(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<html><body>safe body</body></html>")

    plan = build_candidate_acquisition_plan(_candidate(), tmp_path, metadata_result=_metadata())
    result = run_candidate_acquisition(
        plan,
        allow_network=True,
        terms_reviewed=True,
        manual_reviewed=True,
        transport=httpx.MockTransport(handler),
    )

    rendered = json.dumps(result.to_dict(), ensure_ascii=False).lower()
    for forbidden in ["response_body", "raw_text", "metadata_json", "feature_json", "lesson_json", "password", "token", "secret"]:
        assert forbidden not in rendered


def _candidate() -> dict:
    return {
        "title": "Measles data",
        "domain": "www.cdc.gov",
        "url": "https://www.cdc.gov/measles/data-research/index.html",
        "quality_gate_status": "pass",
        "metadata_check_status": "data_source_candidate",
        "data_source_likelihood": 0.7,
        "parser_strategy": "official_report_page",
    }


def _metadata() -> dict:
    return {
        "url": _candidate()["url"],
        "status": "data_source_candidate",
        "data_source_likelihood": 0.7,
        "parser_strategy_hint": "official_report_page",
    }
