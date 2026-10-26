from __future__ import annotations

import json

from live_source_search_orchestrator import LiveSourceSearchRequest, run_live_source_search


def test_metadata_report_files_exist_for_dry_run(tmp_path):
    report = run_live_source_search(
        LiveSourceSearchRequest(
            country="United States",
            disease="measles",
            dry_run=True,
            metadata_check=True,
            output_dir=str(tmp_path),
        )
    )

    assert report.candidate_quality_gate_path
    assert report.metadata_check_results_path
    assert report.refined_review_queue_path
    assert report.metadata_check_safety_audit_path
    payload = json.loads(open(report.metadata_check_results_path, encoding="utf-8").read())
    assert payload["request_count"] == 0
    assert payload["full_body_saved"] is False
