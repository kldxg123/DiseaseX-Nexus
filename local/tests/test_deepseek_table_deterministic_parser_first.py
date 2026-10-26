from __future__ import annotations

import json
from pathlib import Path

from deepseek_web_search_agent.table_extraction_runner import run_structured_table_review


def test_table_review_uses_deterministic_preview_before_deepseek(tmp_path: Path) -> None:
    source_pack = tmp_path / "page_read_source_pack.json"
    source_pack.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "source_id": "SRC1",
                        "title_summary": "Influenza table",
                        "url_safe_summary": "https://example.org/flu-table",
                        "domain": "example.org",
                        "source_type": "html_table",
                        "material_strength": "table_preview",
                        "table_headers": ["Week", "State", "Confirmed cases", "Deaths"],
                        "table_preview_rows": [
                            {"Week": "Week 20", "State": "California", "Confirmed cases": "12", "Deaths": "1"}
                        ],
                        "dated_signal_count": 1,
                        "numeric_signal_count": 2,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    summary = run_structured_table_review(
        disease="influenza",
        country_or_region="United States",
        date_start="2026-05-01",
        date_end="2026-07-01",
        output_dir=str(tmp_path / "review"),
        source_pack_path=str(source_pack),
        mock_deepseek=False,
        allow_deepseek=False,
    )

    assert summary["extraction_mode"] == "deterministic_parser"
    assert summary["deterministic_rows_generated"] == 1
    assert summary["deepseek_called"] is False
    rows = json.loads(Path(summary["paths"]["review_json"]).read_text(encoding="utf-8"))["rows"]
    assert rows[0]["confirmed_cases"] == 12
