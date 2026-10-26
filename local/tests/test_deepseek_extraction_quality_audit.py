from __future__ import annotations

from common.db import get_session
from common.models import DiseaseCountryInformationRecord
from deepseek_web_search_agent.extraction_quality_audit import audit_extracted_records, write_deepseek_quality_audit_report
from tests.deepseek_audit_fixture import create_deepseek_audit_fixture


def test_audit_extracted_records_summarizes_readiness(tmp_path) -> None:
    report_dir, database_url, run_id = create_deepseek_audit_fixture(tmp_path)
    with get_session(database_url) as session:
        records = session.query(DiseaseCountryInformationRecord).filter_by(run_id=run_id).all()
        audits, summary = audit_extracted_records(records)

    assert len(audits) == 3
    assert summary.records_audited == 3
    assert summary.feature_ready_count == 1
    assert summary.event_ready_count == 2
    assert summary.evidence_only_count == 1
    assert summary.requires_human_review_count == 3

    result = write_deepseek_quality_audit_report(report_dir, database_url=database_url)
    assert result["status"] == "ok"
    assert result["records_persisted"] == 3
    assert result["persistence_match"] is True

