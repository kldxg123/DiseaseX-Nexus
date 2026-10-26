from __future__ import annotations

import json

from deepseek_web_search_agent.extraction_quality_audit import write_deepseek_quality_audit_report
from tests.deepseek_audit_fixture import create_deepseek_audit_fixture


def test_readiness_review_api_summary_table_queue_and_export(api_client, tmp_path) -> None:
    report_dir, database_url, _run_id = create_deepseek_audit_fixture(tmp_path)
    write_deepseek_quality_audit_report(report_dir, database_url=database_url)

    summary = api_client.get("/deepseek-web-search/readiness-review/summary", params={"report_dir": str(report_dir)})
    table = api_client.get("/deepseek-web-search/readiness-review/table", params={"report_dir": str(report_dir)})
    queue = api_client.get("/deepseek-web-search/readiness-review/manual-review-queue", params={"report_dir": str(report_dir)})
    export = api_client.post("/deepseek-web-search/readiness-review/export", json={"report_dir": str(report_dir)})

    assert summary.status_code == 200
    assert summary.json()["total_records"] == 3
    assert table.status_code == 200
    assert len(table.json()["records"]) == 3
    assert queue.status_code == 200
    assert len(queue.json()["queue"]) == 3
    assert export.status_code == 200
    rendered = json.dumps(
        {"summary": summary.json(), "table": table.json(), "queue": queue.json(), "export": export.json()},
        ensure_ascii=False,
    ).lower()
    assert "api_key" not in rendered
    assert "prompt" not in rendered
    assert "completion" not in rendered
    assert "response body" not in rendered
    assert "raw_text" not in rendered

