from __future__ import annotations

from deepseek_web_search_agent.extraction_quality_audit import verify_persisted_records
from tests.deepseek_audit_fixture import create_deepseek_audit_fixture


def test_persistence_verification_matches_report_and_database(tmp_path) -> None:
    report_dir, database_url, _run_id = create_deepseek_audit_fixture(tmp_path)

    verification = verify_persisted_records(report_dir, database_url=database_url)

    assert verification.records_extracted_count == 3
    assert verification.records_persisted_count == 3
    assert verification.database_records_count == 3
    assert verification.persistence_match is True
    assert verification.mismatch_warnings == []

