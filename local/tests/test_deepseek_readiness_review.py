from __future__ import annotations

from deepseek_web_search_agent.readiness_review import (
    build_missing_field_explanation,
    build_readiness_review_rows,
    filter_readiness_rows,
    recommend_manual_review_action,
    summarize_readiness_review,
    write_readiness_review_reports,
)
from tests.deepseek_audit_fixture import create_deepseek_audit_fixture


def test_build_readiness_review_rows_explains_record_gaps(tmp_path) -> None:
    report_dir, database_url, _run_id = create_deepseek_audit_fixture(tmp_path)
    from deepseek_web_search_agent.extraction_quality_audit import write_deepseek_quality_audit_report

    write_deepseek_quality_audit_report(report_dir, database_url=database_url)

    rows = build_readiness_review_rows(report_dir)
    summary = summarize_readiness_review(rows)

    assert len(rows) == 3
    assert summary.total_records == 3
    assert summary.feature_ready_count == 1
    assert summary.event_ready_count == 2
    assert summary.evidence_only_count == 1
    evidence_only = [row for row in rows if row.evidence_only][0]
    assert "date_or_iso_week" in evidence_only.missing_fields
    assert "numeric_value" in evidence_only.missing_fields
    assert "Cannot become event-ready" in evidence_only.why_not_event_ready
    assert "Cannot become feature-ready" in evidence_only.why_not_feature_ready
    assert build_missing_field_explanation(evidence_only)
    assert recommend_manual_review_action(evidence_only)


def test_filter_and_write_readiness_review_reports(tmp_path) -> None:
    report_dir, database_url, _run_id = create_deepseek_audit_fixture(tmp_path)
    from deepseek_web_search_agent.extraction_quality_audit import write_deepseek_quality_audit_report

    write_deepseek_quality_audit_report(report_dir, database_url=database_url)
    result = write_readiness_review_reports(report_dir)
    rows = build_readiness_review_rows(report_dir)

    assert result["status"] == "ok"
    assert result["total_records"] == 3
    assert len(filter_readiness_rows(rows, readiness="feature_ready")) == 1
    assert len(filter_readiness_rows(rows, blocker="insufficient_numeric_signal")) == 2
    assert (report_dir / "deepseek_readiness_review_table.json").exists()
    assert (report_dir / "deepseek_manual_review_queue.json").exists()

