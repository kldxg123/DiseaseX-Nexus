from __future__ import annotations

from live_source_search_orchestrator.acquisition_bridge import (
    build_candidate_acquisition_plan,
    list_review_candidates,
    select_acquisition_candidate,
)


def test_select_candidate_joins_legacy_queue_to_metadata_url():
    review_queue = [
        {
            "candidate_id": "a",
            "title": "Cases and Outbreaks",
            "domain": "www.cdc.gov",
            "quality_gate_status": "pass",
            "metadata_check_status": "data_source_candidate",
            "data_source_likelihood": 0.68,
            "source_score": 0.55,
            "refined_score": 0.73,
        }
    ]
    metadata_results = [
        {
            "url": "https://www.cdc.gov/measles/data-research/index.html",
            "domain": "www.cdc.gov",
            "title_summary": "Measles Cases and Outbreaks | CDC",
            "status": "data_source_candidate",
            "data_source_likelihood": 0.68,
        }
    ]

    selected = select_acquisition_candidate(review_queue, metadata_results)

    assert selected is not None
    assert selected["url"] == "https://www.cdc.gov/measles/data-research/index.html"
    assert selected["metadata_result"]["status"] == "data_source_candidate"


def test_select_candidate_skips_rejected_and_low_likelihood():
    review_queue = [
        {
            "candidate_id": "jobs",
            "title": "Public Health Jobs",
            "domain": "jobs.cdc.gov",
            "quality_gate_status": "pass",
            "metadata_check_status": "data_source_candidate",
            "data_source_likelihood": 0.9,
        },
        {
            "candidate_id": "low",
            "title": "Symptoms",
            "domain": "www.cdc.gov",
            "quality_gate_status": "pass",
            "metadata_check_status": "uncertain",
            "data_source_likelihood": 0.2,
        },
    ]
    metadata_results = [
        {"url": "https://jobs.cdc.gov/item", "domain": "jobs.cdc.gov", "status": "data_source_candidate", "data_source_likelihood": 0.9},
        {"url": "https://www.cdc.gov/symptoms", "domain": "www.cdc.gov", "status": "uncertain", "data_source_likelihood": 0.2},
    ]

    assert select_acquisition_candidate(review_queue, metadata_results) is None


def test_build_plan_blocks_when_no_candidate(tmp_path):
    plan = build_candidate_acquisition_plan(None, tmp_path, country="United States", disease="measles")

    assert plan.preflight.allowed is False
    assert "no_candidate_selected" in plan.preflight.reasons
    assert (tmp_path / plan.run_id / "candidate_acquisition_plan.json").exists()


def test_list_review_candidates_enriches_legacy_urls(tmp_path):
    run_dir = _write_run_dir(tmp_path)

    payload = list_review_candidates(run_dir)

    assert payload["selected_candidate"]["url"].startswith("https://www.cdc.gov/")
    assert payload["candidates"][0]["url"].startswith("https://www.cdc.gov/")


def _write_run_dir(tmp_path):
    import json

    run_dir = tmp_path / "search_measles"
    run_dir.mkdir()
    (run_dir / "refined_review_queue.json").write_text(
        json.dumps(
            {
                "review_queue": [
                    {
                        "candidate_id": "a",
                        "title": "Cases and Outbreaks",
                        "domain": "www.cdc.gov",
                        "quality_gate_status": "pass",
                        "metadata_check_status": "data_source_candidate",
                        "data_source_likelihood": 0.68,
                        "source_score": 0.55,
                        "refined_score": 0.73,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "metadata_check_results.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "url": "https://www.cdc.gov/measles/data-research/index.html",
                        "domain": "www.cdc.gov",
                        "title_summary": "Measles Cases and Outbreaks | CDC",
                        "status": "data_source_candidate",
                        "data_source_likelihood": 0.68,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "candidate_quality_gate.json").write_text('{"candidate_quality_gate":[]}', encoding="utf-8")
    (run_dir / "live_source_search_summary.json").write_text(
        '{"country":"United States","disease":"measles"}',
        encoding="utf-8",
    )
    return run_dir
