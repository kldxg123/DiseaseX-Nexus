from __future__ import annotations

import json
from pathlib import Path

from tests.deepseek_table_review_fixture import create_mock_table_review


def test_mock_table_review_writes_staging_files_without_raw_payloads(tmp_path) -> None:
    summary, review_path = create_mock_table_review(tmp_path)
    run_dir = Path(summary["run_dir"])

    assert summary["status"] == "ok"
    assert summary["deepseek_called"] is False
    assert review_path.exists()
    for name in [
        "query_request.json",
        "source_pack.json",
        "source_map.json",
        "structured_event_table_review.csv",
        "validation_issues.json",
        "duplicate_report.json",
        "approval_status_summary.json",
        "ingestion_plan.md",
        "safety_audit.md",
        "next_action_plan.md",
    ]:
        assert (run_dir / name).exists()

    rendered = json.dumps(json.loads((run_dir / "deepseek_table_extraction_summary.json").read_text(encoding="utf-8")), ensure_ascii=False).lower()
    assert "prompt" not in rendered
    assert "completion" not in rendered
    assert "raw_text" not in rendered
    assert json.loads((run_dir / "approval_status_summary.json").read_text(encoding="utf-8"))["pending_review"] == 2
