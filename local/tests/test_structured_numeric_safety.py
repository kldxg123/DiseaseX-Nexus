from __future__ import annotations

import json

from deepseek_web_search_agent.query_request import build_disease_time_query_request
from deepseek_web_search_agent.source_pack_builder import load_source_pack, source_pack_to_dict
from deepseek_web_search_agent.structured_table_types import SourceEvidenceItem
from deepseek_web_search_agent.deterministic_table_extractor import deterministic_rows_from_sources


def test_source_pack_does_not_preserve_raw_html_or_raw_text(tmp_path) -> None:
    path = tmp_path / "page_read_summary.json"
    path.write_text(
        json.dumps(
            {
                "page_reads": [
                    {
                        "url": "https://example.org/report",
                        "domain": "example.org",
                        "title": "Ebola report",
                        "raw_text": "do not keep full text",
                        "html": "<html>do not keep html</html>",
                        "text_summary": "Safe clipped summary.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    payload = source_pack_to_dict(load_source_pack(path))
    serialized = json.dumps(payload, ensure_ascii=False)

    assert "raw_text" not in serialized
    assert "<html>" not in serialized
    assert "Safe clipped summary" in serialized


def test_deterministic_extractor_does_not_fabricate_without_date() -> None:
    request = build_disease_time_query_request(
        disease="Ebola",
        country_or_region="African Region",
        date_start="2026-05-01",
        date_end="2026-07-01",
    )
    source = SourceEvidenceItem(
        source_id="SRC1",
        title_summary="Ebola overview",
        url_safe_summary="https://example.org/overview",
        domain="example.org",
        paragraph_signal_preview=[{"text": "The report mentioned 12 confirmed cases but no explicit date."}],
    )

    assert deterministic_rows_from_sources([source], request) == []
