from __future__ import annotations

import json

from scripts.audit_deepseek_structured_extraction import main
from tests.deepseek_audit_fixture import create_deepseek_audit_fixture


def test_deepseek_extraction_audit_cli_outputs_safe_summary(tmp_path, capsys) -> None:
    report_dir, database_url, _run_id = create_deepseek_audit_fixture(tmp_path)

    exit_code = main(["--report-dir", str(report_dir), "--database-url", database_url])
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["records_extracted"] == 3
    rendered = output.lower()
    assert "api_key" not in rendered
    assert "prompt" not in rendered
    assert "completion" not in rendered

