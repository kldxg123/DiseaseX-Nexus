from __future__ import annotations

import httpx

from apps.dashboard.api_client import DashboardApiClient
from apps.dashboard.pages import _deepseek_table_issue_rows, _deepseek_table_review_rows, _deepseek_table_source_rows


def test_dashboard_api_client_exposes_table_review_methods() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"status": "ok", "summary": {"deepseek_called": False}})

    client = DashboardApiClient(base_url="http://testserver", transport=httpx.MockTransport(handler))
    payload = {"disease": "Ebola", "country_or_region": "African Region", "date_start": "2026-05-01", "date_end": "2026-07-01"}

    client.deepseek_table_review_build_source_pack(payload)
    client.deepseek_table_review_run_mock(payload)
    client.deepseek_table_review_run_deepseek(payload)
    client.deepseek_table_review_validate({"review_table": "review.json"})
    client.deepseek_table_review_clean_validate({"review_table": "review.json"})
    client.deepseek_table_review_export_review_table({"review_table": "review.json"})
    client.deepseek_table_review_preview_ingestion({"review_table": "review.json"})
    client.deepseek_table_review_visualization("run1", output_dir="reports")
    client.deepseek_table_review_ingest_approved({"review_table": "review.json", "allow_persistence": False})

    assert seen == [
        "/deepseek-web-search/table-review/build-source-pack",
        "/deepseek-web-search/table-review/run-mock",
        "/deepseek-web-search/table-review/run-deepseek",
        "/deepseek-web-search/table-review/validate",
        "/deepseek-web-search/table-review/clean-validate",
        "/deepseek-web-search/table-review/export-review-table",
        "/deepseek-web-search/table-review/preview-ingestion",
        "/deepseek-web-search/table-review/visualization/run1",
        "/deepseek-web-search/table-review/ingest-approved",
    ]


def test_dashboard_table_review_rows_are_safe_and_human_readable() -> None:
    source_rows = _deepseek_table_source_rows([{"source_id": "SRC1", "title_summary": "Title", "domain": "example.org", "raw_text": "hidden"}])
    review_rows = _deepseek_table_review_rows([{"row_id": "ROW1", "date": "2026-05-01", "country": "X", "confirmed_cases": 1, "feature_json": {"hidden": True}}])
    issue_rows = _deepseek_table_issue_rows([{"row_id": "ROW1", "field": "date", "issue_type": "missing", "message": "fix"}])

    assert source_rows[0]["title"] == "Title"
    assert "raw_text" not in source_rows[0]
    assert review_rows[0]["confirmed_cases"] == 1
    assert "feature_json" not in review_rows[0]
    assert issue_rows[0]["issue_type"] == "missing"
