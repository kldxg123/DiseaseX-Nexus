from __future__ import annotations

from deepseek_web_search_agent.query_request import build_disease_time_query_request
from deepseek_web_search_agent.source_pack_builder import load_source_pack
from deepseek_web_search_agent.staging_review import write_structured_table_review


def test_zero_row_review_writes_bundle_level_blockers(tmp_path) -> None:
    source_path = tmp_path / "source_pack.json"
    source_path.write_text(
        '{"sources":[{"title":"Search only","url":"https://example.org","snippet":"overview"}]}',
        encoding="utf-8",
    )
    request = build_disease_time_query_request(
        disease="influenza",
        country_or_region="United States",
        date_start="2026-05-01",
        date_end="2026-07-01",
    )
    sources = load_source_pack(source_path)

    bundle = write_structured_table_review(
        output_dir=tmp_path,
        query_request=request,
        sources=sources,
        extracted_rows=[],
        cleaned_rows=[],
        validation_issues=[],
        duplicate_groups=[],
        prompt_hash="hash",
        prompt_summary={"source_count": 1},
        deepseek_called=False,
        source_pack_readiness={"source_pack_level_blockers": ["search_result_only_material"]},
        deterministic_rows_generated=0,
        deepseek_rows_generated=0,
        extraction_mode="insufficient_material",
    )

    issues = (tmp_path / "validation_issues.json").read_text(encoding="utf-8")
    assert "no_structured_rows_extracted" in issues
    assert "insufficient_source_material" in issues
    assert bundle.cleaned_rows == []
