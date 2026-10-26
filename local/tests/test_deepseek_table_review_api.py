from __future__ import annotations

import json
from pathlib import Path


def _payload(tmp_path) -> dict:
    return {
        "disease": "Ebola",
        "country_or_region": "African Region",
        "date_start": "2026-05-01",
        "date_end": "2026-07-01",
        "output_dir": str(tmp_path / "api-table-review"),
    }


def test_deepseek_table_review_api_mock_validate_and_ingest(api_client, tmp_path) -> None:
    source_pack = api_client.post("/deepseek-web-search/table-review/build-source-pack", json=_payload(tmp_path))
    run_mock = api_client.post("/deepseek-web-search/table-review/run-mock", json=_payload(tmp_path))

    assert source_pack.status_code == 200
    assert source_pack.json()["deepseek_called"] is False
    assert run_mock.status_code == 200
    summary = run_mock.json()["summary"]
    review_path = Path(summary["review_json"])
    validate = api_client.post("/deepseek-web-search/table-review/clean-validate", json={"review_table": str(review_path)})
    assert validate.status_code == 200
    assert validate.json()["issues_count"] == 0
    assert validate.json()["visualization"]["visualization_summary"]["rows_total"] == 2
    export = api_client.post("/deepseek-web-search/table-review/export-review-table", json={"review_table": str(review_path)})
    preview_before = api_client.post("/deepseek-web-search/table-review/preview-ingestion", json={"review_table": str(review_path)})
    assert export.status_code == 200
    assert preview_before.status_code == 200
    assert preview_before.json()["approved_rows_seen"] == 0

    payload = json.loads(review_path.read_text(encoding="utf-8"))
    for row in payload["rows"]:
        row["approval_status"] = "approved"
    review_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    database_url = f"sqlite:///{(tmp_path / 'api-approved.sqlite').as_posix()}"
    ingest = api_client.post(
        "/deepseek-web-search/table-review/ingest-approved",
        json={"review_table": str(review_path), "database_url": database_url, "allow_persistence": True},
    )
    preview_after = api_client.post("/deepseek-web-search/table-review/preview-ingestion", json={"review_table": str(review_path)})
    run_id = summary["run_id"]
    visualization = api_client.get(
        f"/deepseek-web-search/table-review/visualization/{run_id}",
        params={"output_dir": str(tmp_path / "api-table-review")},
    )

    assert ingest.status_code == 200
    assert preview_after.json()["epidemic_events_would_create"] == 2
    assert visualization.status_code == 200
    assert visualization.json()["visualization_summary"]["rows_total"] == 2
    assert ingest.json()["summary"]["epidemic_events_created"] == 2
    rendered = json.dumps(ingest.json(), ensure_ascii=False).lower()
    assert "prompt" not in rendered
    assert "completion" not in rendered
    assert "raw_text" not in rendered


def test_deepseek_table_review_api_blocks_live_without_explicit_allow(api_client, tmp_path) -> None:
    response = api_client.post("/deepseek-web-search/table-review/run-deepseek", json=_payload(tmp_path))

    assert response.status_code == 200
    assert response.json()["status"] == "blocked"
    assert response.json()["summary"]["deepseek_called"] is False
