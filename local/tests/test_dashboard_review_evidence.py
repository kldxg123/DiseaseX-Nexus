from __future__ import annotations

import json
import inspect

from apps.dashboard import pages


def test_source_evidence_rows_are_safe_and_summarized(tmp_path) -> None:
    source_map = tmp_path / "source_map.json"
    source_map.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "source_id": "SRC1",
                        "domain": "example.org",
                        "title_summary": "Weekly disease report",
                        "evidence_summary": " ".join(["case counts by week"] * 80),
                        "material_strength": "table_preview",
                        "extraction_readiness": "ready_for_table_extraction",
                        "dated_signal_count": 2,
                        "numeric_signal_count": 3,
                        "table_preview_rows_count": 5,
                        "extraction_blockers": [],
                        "url_safe_summary": "https://example.org/report",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    rows = pages._source_evidence_rows(source_map)

    assert rows[0]["网站"] == "example.org"
    assert rows[0]["日期信号"] == 2
    assert rows[0]["数值信号"] == 3
    assert rows[0]["表格预览行"] == 5
    assert len(rows[0]["网页摘要"]) <= 260


def test_demo_step_uses_more_sources_and_direct_review_workspace() -> None:
    source = inspect.getsource(pages.render_deepseek_demo_pipeline_page)

    assert '"max_sources": 8' in source
    assert "_render_table_review_workspace(st, client, review_path)" in source
    assert "_render_source_pack_evidence(st, state.get(\"demo_last_source_pack\"))" in source


def test_latest_review_table_prefers_non_empty_over_newer_zero_row(tmp_path) -> None:
    older = tmp_path / "older" / "structured_event_table_review.json"
    newer = tmp_path / "newer" / "structured_event_table_review.json"
    older.parent.mkdir()
    newer.parent.mkdir()
    older.write_text(json.dumps({"rows": [{"row_id": "ROW1"}]}, ensure_ascii=False), encoding="utf-8")
    newer.write_text(json.dumps({"rows": []}, ensure_ascii=False), encoding="utf-8")

    assert pages._latest_or_remembered_review_table({}, tmp_path) == str(older)
