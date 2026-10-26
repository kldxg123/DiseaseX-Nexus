from __future__ import annotations

import json

from tests.deepseek_audit_fixture import create_deepseek_audit_fixture


def test_deepseek_extraction_audit_api_returns_safe_summary(api_client, tmp_path) -> None:
    report_dir, database_url, _run_id = create_deepseek_audit_fixture(tmp_path)

    response = api_client.post(
        "/deepseek-web-search/extraction-quality-audit",
        json={"report_dir": str(report_dir), "database_url": database_url},
    )

    assert response.status_code == 200
    summary = response.json()["summary"]
    assert summary["records_extracted"] == 3
    assert summary["persistence_match"] is True
    rendered = json.dumps(response.json(), ensure_ascii=False).lower()
    assert "api_key" not in rendered
    assert "prompt" not in rendered
    assert "completion" not in rendered


def test_deepseek_extraction_audit_api_missing_report_dir_is_safe(api_client, tmp_path) -> None:
    missing_report_dir = tmp_path / "missing-replay-report"

    response = api_client.post(
        "/deepseek-web-search/extraction-quality-audit",
        json={"report_dir": str(missing_report_dir)},
    )

    assert response.status_code == 200
    summary = response.json()["summary"]
    assert summary["status"] in {"limited", "failed"}
    assert summary["deepseek_called_in_this_step"] is False
    assert summary["tavily_rerun_in_this_step"] is False
    assert summary["candidate_urls_accessed"] is False
    assert "warnings" in summary
    assert missing_report_dir.exists()
    rendered = json.dumps(response.json(), ensure_ascii=False).lower()
    assert "prompt" not in rendered
    assert "completion" not in rendered
    assert "response_body" not in rendered
