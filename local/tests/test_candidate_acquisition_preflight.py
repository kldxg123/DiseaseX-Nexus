from __future__ import annotations

from live_source_search_orchestrator.acquisition_bridge import build_candidate_acquisition_plan


def test_preflight_allows_metadata_confirmed_candidate(tmp_path):
    candidate = {
        "title": "Cases and Outbreaks",
        "domain": "www.cdc.gov",
        "url": "https://www.cdc.gov/measles/data-research/index.html",
        "quality_gate_status": "pass",
        "metadata_check_status": "data_source_candidate",
        "data_source_likelihood": 0.68,
        "parser_strategy": "html_table",
    }
    metadata = {
        "url": candidate["url"],
        "status": "data_source_candidate",
        "data_source_likelihood": 0.68,
        "parser_strategy_hint": "html_table",
    }

    plan = build_candidate_acquisition_plan(candidate, tmp_path, metadata_result=metadata, max_requests=1)

    assert plan.preflight.allowed is True
    assert plan.preflight.request_count_limit == 1
    assert plan.preflight.follow_links is False


def test_preflight_blocks_multi_request_or_unconfirmed_candidate(tmp_path):
    candidate = {
        "title": "Uncertain page",
        "domain": "www.cdc.gov",
        "url": "https://www.cdc.gov/example",
        "quality_gate_status": "pass",
        "metadata_check_status": "uncertain",
        "data_source_likelihood": 0.2,
    }

    plan = build_candidate_acquisition_plan(
        candidate,
        tmp_path,
        metadata_result={"status": "uncertain", "data_source_likelihood": 0.2},
        max_requests=2,
    )

    assert plan.preflight.allowed is False
    assert "max_requests_must_be_lte_1" in plan.preflight.reasons
