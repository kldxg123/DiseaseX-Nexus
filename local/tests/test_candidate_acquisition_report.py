from __future__ import annotations

import json

import httpx

from live_source_search_orchestrator.acquisition_bridge import (
    build_candidate_acquisition_plan,
    run_candidate_acquisition,
)


def test_candidate_acquisition_report_files_are_written(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<html><body>Measles cases table</body></html>")

    plan = build_candidate_acquisition_plan(_candidate(), tmp_path, metadata_result=_metadata(), country="United States", disease="measles")
    result = run_candidate_acquisition(
        plan,
        allow_network=True,
        terms_reviewed=True,
        manual_reviewed=True,
        transport=httpx.MockTransport(handler),
    )

    expected_files = [
        "candidate_acquisition_plan.json",
        "candidate_acquisition_summary.json",
        "candidate_acquisition_safety_audit.md",
        "connector_document_summary.json",
        "raw_document_ingest_summary.json",
        "event_extraction_summary.json",
        "feature_generation_summary.json",
        "next_connector_recommendation.json",
        "rollback_commands.md",
    ]
    for name in expected_files:
        assert (tmp_path / result.run_id / name).exists()

    summary = json.loads((tmp_path / result.run_id / "candidate_acquisition_summary.json").read_text(encoding="utf-8"))
    assert summary["follow_links"] is False
    assert summary["files_downloaded"] is False
    assert "response_body" not in json.dumps(summary).lower()


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
    return {"url": _candidate()["url"], "status": "data_source_candidate", "data_source_likelihood": 0.7}
