from __future__ import annotations

import inspect

from apps.dashboard import pages


def test_dashboard_exposes_structured_numeric_controls_without_json_requirement() -> None:
    source = inspect.getsource(pages.render_deepseek_demo_pipeline_page)

    assert "结构化数值证据模式" in source
    assert "优先 situation reports" in source
    assert "优先日期+数值来源" in source
    assert "保留报告/PDF 候选但不下载" in source
    assert '"query_intent": "structured_numeric_evidence"' in source
    assert '"prefer_dated_numeric_sources"' in source
    assert '"do_not_download_documents": True' in source


def test_dashboard_material_summary_reads_structured_numeric_counts(tmp_path) -> None:
    path = tmp_path / "source_pack_readiness.json"
    path.write_text(
        """
        {
          "source_count": 2,
          "material_strength_counts": {"situation_report_preview": 1},
          "extraction_readiness_counts": {"ready_for_table_extraction": 1},
          "blocker_counts": {},
          "dated_numeric_signal_count": 3,
          "paragraph_signal_preview_count": 2,
          "table_preview_count": 1,
          "document_candidate_count": 1
        }
        """,
        encoding="utf-8",
    )

    summary = pages._source_pack_material_summary(str(path))

    assert summary["dated_numeric_signal_count"] == 3
    assert summary["paragraph_signal_preview_count"] == 2
    assert summary["table_preview_count"] == 1
    assert summary["document_candidate_count"] == 1
