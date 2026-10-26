from __future__ import annotations

import re

from deepseek_web_search_agent.extraction_quality_audit import write_deepseek_quality_audit_report
from tests.deepseek_audit_fixture import create_deepseek_audit_fixture


SECRET_LIKE = re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{18,}\b")


def test_deepseek_extraction_audit_reports_do_not_leak_sensitive_or_raw_payloads(tmp_path) -> None:
    report_dir, database_url, _run_id = create_deepseek_audit_fixture(tmp_path)
    write_deepseek_quality_audit_report(report_dir, database_url=database_url)

    rendered = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in report_dir.glob("deepseek_*")
        if path.suffix.lower() in {".json", ".md"}
    )
    lowered = rendered.lower()

    assert not SECRET_LIKE.search(rendered)
    for forbidden in [
        "api_key",
        "token",
        "password",
        "secret",
        "prompt",
        "completion",
        "response body",
        "raw_text",
        "<html",
        "metadata_json",
        "feature_json",
        "provenance_json",
        "lesson_json",
    ]:
        assert forbidden not in lowered
