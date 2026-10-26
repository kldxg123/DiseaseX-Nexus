from __future__ import annotations

import json
from pathlib import Path

from deepseek_web_search_agent.query_request import build_disease_time_query_request
from deepseek_web_search_agent.source_pack_builder import load_source_pack, source_pack_readiness
from deepseek_web_search_agent.staging_review import write_structured_table_review


def test_zero_rows_write_bundle_level_diagnostics(tmp_path: Path) -> None:
    request = build_disease_time_query_request(
        disease="influenza",
        country_or_region="United States",
        date_start="2026-05-01",
        date_end="2026-07-01",
    )
    source_path = tmp_path / "search_results_summary.json"
    source_path.write_text(
        '{"results":[{"title":"Flu overview","url":"https://example.org/flu","domain":"example.org","snippet":"overview only"}]}',
        encoding="utf-8",
    )
    sources = load_source_pack(source_path)
    bundle = write_structured_table_review(
        output_dir=tmp_path / "review",
        query_request=request,
        sources=sources,
        extracted_rows=[],
        cleaned_rows=[],
        validation_issues=[],
        duplicate_groups=[],
        source_pack_readiness=source_pack_readiness(sources),
        extraction_mode="insufficient_material",
    )

    issues = json.loads(Path(bundle.report_paths["validation_issues"]).read_text(encoding="utf-8"))["issues"]
    issue_types = {issue["issue_type"] for issue in issues}

    assert "no_structured_rows_extracted" in issue_types
    assert "insufficient_source_material" in issue_types
    assert "no_dated_numeric_signal" in issue_types
    assert "no_table_csv_json_preview" in issue_types
