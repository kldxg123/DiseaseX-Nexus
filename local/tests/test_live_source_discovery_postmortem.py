from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.analyze_live_source_discovery_run import analyze_live_source_discovery_run


def _synthetic_429_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "gdelt_influenza_live_429"
    run_dir.mkdir()
    (run_dir / "live_source_discovery_summary.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "provider": "gdelt",
                "country": "United States",
                "disease": "influenza",
                "true_request_count": 1,
                "network_accessed": True,
                "safe_failure_reason": "provider returned no search results",
                "provider_response_summary": {"status_code": 429, "content_hash_prefix": "abc123"},
                "candidate_urls_accessed": False,
                "follow_links": False,
                "search_results_seen": 0,
                "candidates_found": 0,
                "connector_specs_generated": 0,
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "source_discovery_report.json").write_text(
        json.dumps({"search_results_seen": 0, "candidates_found": 0, "connector_specs_generated": 0}),
        encoding="utf-8",
    )
    (run_dir / "connector_specs_summary.json").write_text(
        json.dumps({"connector_specs_generated": 0, "specs": []}),
        encoding="utf-8",
    )
    (run_dir / "rollback_commands.md").write_text("# rollback\n", encoding="utf-8")
    db_path = run_dir / "gdelt_live_discovery.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE source_registry (id INTEGER, status TEXT)")
    return run_dir


def test_postmortem_reads_synthetic_429_run_and_generates_reports(tmp_path) -> None:
    run_dir = _synthetic_429_run(tmp_path)

    result = analyze_live_source_discovery_run(run_dir)

    assert result["status"] == "ok"
    assert result["failure_type"] == "rate_limited"
    assert result["retry_allowed"] is False
    assert result["cooldown_recommended"] is True
    assert result["minimum_cooldown_minutes"] >= 60
    assert result["whether_retry_happened"] is False
    assert result["whether_candidate_urls_were_visited"] is False
    assert Path(result["postmortem_json"]).exists()
    assert Path(result["postmortem_markdown"]).exists()
    rendered = json.dumps(result, ensure_ascii=False).lower()
    assert "metadata_json" not in rendered
    assert "raw_text" not in rendered
    assert "response body" not in rendered
