from __future__ import annotations

from deepseek_web_search_agent.source_pack_builder import load_source_pack, source_pack_readiness


def test_source_pack_material_strength_counts_search_only_and_preview(tmp_path) -> None:
    path = tmp_path / "source_pack.json"
    path.write_text(
        """
        {"sources": [
          {"title": "Search only", "url": "https://example.org/news", "snippet": "overview"},
          {
            "title": "Table preview",
            "url": "https://example.org/table",
            "table_headers": ["Week", "Cases"],
            "table_preview_rows": [{"Week": "Week 20", "Cases": "12"}],
            "dated_signal_count": 1,
            "numeric_signal_count": 2
          }
        ]}
        """,
        encoding="utf-8",
    )

    sources = load_source_pack(path)
    readiness = source_pack_readiness(sources)

    assert readiness["material_strength_counts"]["search_result_only"] == 1
    assert readiness["material_strength_counts"]["table_preview"] == 1
    assert readiness["ready_for_table_extraction"] is True
