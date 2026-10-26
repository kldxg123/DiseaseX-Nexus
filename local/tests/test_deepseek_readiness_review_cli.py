from __future__ import annotations

import json

from deepseek_web_search_agent.extraction_quality_audit import write_deepseek_quality_audit_report
from scripts.build_deepseek_readiness_review import main
from tests.deepseek_audit_fixture import create_deepseek_audit_fixture


def test_build_deepseek_readiness_review_cli_outputs_safe_summary(tmp_path, capsys) -> None:
    report_dir, database_url, _run_id = create_deepseek_audit_fixture(tmp_path)
    write_deepseek_quality_audit_report(report_dir, database_url=database_url)

    code = main(["--report-dir", str(report_dir)])
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert code == 0
    assert payload["status"] == "ok"
    assert payload["total_records"] == 3
    assert payload["manual_review_queue"]
    rendered = output.lower()
    assert "api_key" not in rendered
    assert "prompt" not in rendered
    assert "completion" not in rendered

