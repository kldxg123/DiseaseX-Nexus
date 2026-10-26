from __future__ import annotations

from deepseek_web_search_agent.deepseek_table_prompt import build_structured_event_table_prompt, prompt_summary
from deepseek_web_search_agent.query_request import build_disease_time_query_request
from deepseek_web_search_agent.source_pack_builder import load_source_pack


def test_table_prompt_includes_material_strength_without_raw_html(tmp_path) -> None:
    path = tmp_path / "source_pack.json"
    path.write_text(
        """
        {"sources": [{
          "source_id": "SRC1",
          "title_summary": "Flu table",
          "url_safe_summary": "https://example.org/flu",
          "domain": "example.org",
          "evidence_summary": "Limited table preview",
          "material_strength": "table_preview",
          "table_headers": ["Week", "Confirmed cases"],
          "table_preview_rows": [{"Week": "Week 20", "Confirmed cases": "12"}],
          "dated_signal_count": 1,
          "numeric_signal_count": 2
        }]}
        """,
        encoding="utf-8",
    )
    request = build_disease_time_query_request(
        disease="influenza",
        country_or_region="United States",
        date_start="2026-05-01",
        date_end="2026-07-01",
    )
    sources = load_source_pack(path)

    prompt = build_structured_event_table_prompt(request, sources)
    summary = prompt_summary(request, sources)

    assert "material_strength" in prompt
    assert "search_result_only" in prompt
    assert "raw HTML" not in prompt
    assert summary["material_strength_counts"]["table_preview"] == 1
