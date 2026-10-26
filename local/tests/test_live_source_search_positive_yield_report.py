from __future__ import annotations

import json

from live_source_search_orchestrator.orchestrator import build_positive_yield_validation
from live_source_search_orchestrator.report import make_run_dir, write_live_source_search_report
from live_source_search_orchestrator.types import (
    LiveSourceSearchReport,
    LiveSourceSearchRequest,
    ProviderPlan,
    ProviderSearchResult,
)


def test_positive_yield_validation_fields():
    request = LiveSourceSearchRequest(country="United States", disease="measles", dry_run=False)

    validation = build_positive_yield_validation(
        request=request,
        candidates_seen=2,
        candidates_after_dedupe=1,
        pending_review_inserted=1,
        connector_specs_generated=1,
        request_count_total=2,
    )

    assert validation["achieved_positive_yield"] is True
    assert validation["achieved_without_candidate_url_visit"] is True
    assert validation["achieved_without_follow_links"] is True
    assert validation["request_budget_respected"] is True


def test_report_writes_positive_yield_and_provider_diagnostics(tmp_path):
    report = LiveSourceSearchReport(
        status="ok",
        run_id="search_measles_test",
        country="United States",
        disease="measles",
        aliases_used=["measles", "rubeola"],
        providers_planned=["official_directory"],
        providers_used=["official_directory"],
        request_count_total=1,
        provider_request_counts={"official_directory": 1},
        candidates_seen=1,
        candidates_after_dedupe=1,
        pending_review_inserted=1,
        connector_specs_generated=1,
        top_domains=["www.cdc.gov"],
        failure_types={"official_directory": None},
        warnings=[],
        provider_failure_diagnostics=[
            {
                "provider": "official_directory",
                "status": "ok",
                "failure_type": None,
                "http_status_code": 200,
                "request_count": 1,
                "network_accessed": True,
                "retry_allowed": False,
                "safe_failure": True,
                "sanitized_error_summary": None,
                "candidate_urls_visited": False,
                "follow_links": False,
            }
        ],
        positive_yield_validation={
            "disease": "measles",
            "country": "United States",
            "candidates_seen": 1,
            "candidates_after_dedupe": 1,
            "pending_review_inserted": 1,
            "connector_specs_generated": 1,
            "achieved_positive_yield": True,
            "achieved_without_candidate_url_visit": True,
            "achieved_without_follow_links": True,
            "request_budget_respected": True,
            "limitations": "safe summary",
        },
    )
    run_dir = make_run_dir(output_dir=tmp_path, country="United States", disease="measles", timestamp="20260101T000000Z")

    paths = write_live_source_search_report(
        run_dir=run_dir,
        report=report,
        provider_plan=ProviderPlan("auto", ["official_directory"], 3),
        provider_results=[
            ProviderSearchResult(
                provider="official_directory",
                status="ok",
                request_count=1,
                search_results=[],
                network_accessed=True,
            )
        ],
        normalized_candidates=[],
        deduped_candidates=[],
        review_queue=[],
        connector_specs=[],
    )

    assert json.loads((run_dir / "positive_yield_validation.json").read_text(encoding="utf-8"))[
        "achieved_positive_yield"
    ] is True
    assert (run_dir / "provider_failure_diagnostics.md").exists()
    assert paths["positive_yield_validation_json"].endswith("positive_yield_validation.json")
