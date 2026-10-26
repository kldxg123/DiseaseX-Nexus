from __future__ import annotations

import json

from deepseek_web_search_agent.source_pack_builder import load_source_pack, source_pack_to_dict


def test_source_pack_recovery_does_not_preserve_raw_html_fields(tmp_path) -> None:
    path = tmp_path / "page_read_summary.json"
    path.write_text(
        json.dumps(
            {
                "page_reads": [
                    {
                        "url": "https://example.org/table",
                        "domain": "example.org",
                        "title": "Table",
                        "text_summary": "safe summary",
                        "html": "<html>do not keep</html>",
                        "raw_text": "do not keep",
                        "hints": {
                            "table_headers": ["Week", "Cases"],
                            "table_preview_rows": [{"Week": "Week 20", "Cases": "12"}],
                            "date_terms_count": 1,
                            "numeric_terms_count": 1,
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    payload = source_pack_to_dict(load_source_pack(path))
    text = json.dumps(payload, ensure_ascii=False)

    assert "do not keep" not in text
    assert "<html>" not in text
    assert "table_preview_rows" in text
