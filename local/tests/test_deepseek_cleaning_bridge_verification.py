from __future__ import annotations

from deepseek_web_search_agent.extraction_quality_audit import audit_cleaning_bridge_outputs
from tests.deepseek_audit_fixture import create_deepseek_audit_fixture


def test_cleaning_bridge_verification_reports_local_blockers(tmp_path) -> None:
    report_dir, _database_url, _run_id = create_deepseek_audit_fixture(tmp_path)

    verification = audit_cleaning_bridge_outputs(report_dir)

    assert verification.status == "limited"
    assert verification.raw_documents_created == 0
    assert verification.epidemic_events_created == 0
    assert "requires_human_review" in verification.blockers

