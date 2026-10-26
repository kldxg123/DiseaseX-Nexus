from __future__ import annotations

import json
from pathlib import Path

from deepseek_web_search_agent.approval_ingestion import ingest_approved_table_rows, preview_approved_table_ingestion
from deepseek_web_search_agent.who_downloadable_targeter import build_who_query_plan, run_who_data_acquisition


def test_who_query_plan_is_who_first() -> None:
    plan = build_who_query_plan()

    assert any("site:who.int" in query for query in plan.queries)
    assert any("site:afro.who.int" in query for query in plan.queries)
    assert plan.preferred_data_types[0] == "csv_dataset"


def test_who_dry_run_generates_review_bundle_without_network(tmp_path) -> None:
    result = run_who_data_acquisition(output_dir=str(tmp_path), dry_run=True)
    payload = result.to_dict()

    assert result.status == "ok"
    assert result.network_accessed is False
    assert result.provider_request_count == 0
    assert result.candidates_found >= 3
    assert result.review_rows_generated >= 3
    assert result.pending_review_rows == result.review_rows_generated
    assert Path(result.paths["review_json"]).exists()
    assert Path(result.paths["field_mapping_report"]).exists()
    rendered = json.dumps(payload, ensure_ascii=False).lower()
    assert "raw_text" not in rendered
    assert "response_body" not in rendered
    assert "prompt" not in rendered


def test_who_approved_rows_ingest_only_after_approval(tmp_path) -> None:
    result = run_who_data_acquisition(output_dir=str(tmp_path), dry_run=True)
    review_path = Path(result.paths["review_json"])
    preview_before = preview_approved_table_ingestion(review_table_path=str(review_path))
    assert preview_before["approved_rows_seen"] == 0

    payload = json.loads(review_path.read_text(encoding="utf-8"))
    payload["rows"][0]["approval_status"] = "approved"
    review_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    database_url = f"sqlite:///{(tmp_path / 'who-approved.sqlite').as_posix()}"
    ingested = ingest_approved_table_rows(
        review_table_path=str(review_path),
        database_url=database_url,
        allow_persistence=True,
        output_dir=str(tmp_path / "ingestion"),
    )

    assert ingested["approved_rows_seen"] == 1
    assert ingested["epidemic_events_created"] == 1
    assert ingested["feature_generation_allowed"] is False
