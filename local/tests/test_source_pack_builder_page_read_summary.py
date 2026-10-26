from __future__ import annotations

import json

from deepseek_web_search_agent.source_pack_builder import load_source_pack, source_pack_readiness


def test_source_pack_builder_uses_page_read_summary_table_preview(tmp_path) -> None:
    path = tmp_path / "page_read_summary.json"
    path.write_text(
        json.dumps(
            {
                "page_reads": [
                    {
                        "url": "https://example.org/flu-table",
                        "domain": "example.org",
                        "title": "Flu weekly table",
                        "text_summary": "Weekly influenza table.",
                        "status": "ok",
                        "hints": {
                            "table_count": 1,
                            "table_headers": ["Week", "State", "Confirmed cases"],
                            "table_preview_rows": [{"Week": "Week 20", "State": "California", "Confirmed cases": "12"}],
                            "table_preview_rows_count": 1,
                            "date_terms_count": 1,
                            "numeric_terms_count": 2,
                            "date_or_week_terms": ["Week 20"],
                            "numeric_terms": ["12", "cases"],
                            "has_table_hint": True,
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    sources = load_source_pack(path)
    readiness = source_pack_readiness(sources)

    assert sources[0].material_strength == "table_preview"
    assert sources[0].has_table_preview is True
    assert sources[0].table_headers == ["Week", "State", "Confirmed cases"]
    assert sources[0].table_preview_rows_count == 1
    assert sources[0].extraction_readiness == "ready_for_table_extraction"
    assert readiness["ready_for_table_extraction"] is True
