from __future__ import annotations

from pathlib import Path

from live_source_search_orchestrator import LiveSourceSearchRequest, run_live_source_search


def test_live_source_search_report_writes_expected_files(tmp_path):
    report = run_live_source_search(
        LiveSourceSearchRequest(
            country="United States",
            disease="cholera",
            dry_run=True,
            output_dir=str(tmp_path),
        )
    )
    assert report.output_dir
    target = Path(report.output_dir)
    for name in [
        "live_source_search_summary.json",
        "provider_plan.json",
        "provider_results_summary.json",
        "normalized_candidates.json",
        "deduped_ranked_candidates.json",
        "review_queue.json",
        "connector_specs_recommendations.json",
        "safety_audit.md",
        "rollback_commands.md",
    ]:
        assert (target / name).exists()
    assert report.candidate_urls_visited is False
    assert report.no_follow_links is True
