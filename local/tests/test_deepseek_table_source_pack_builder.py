from __future__ import annotations

import json

from deepseek_web_search_agent.query_request import build_disease_time_query_request
from deepseek_web_search_agent.source_pack_builder import build_source_pack, load_source_pack, mock_source_pack


def test_source_pack_builder_loads_safe_sources_from_review_queue(tmp_path) -> None:
    path = tmp_path / "review_queue.json"
    path.write_text(
        json.dumps(
            {
                "review_queue": [
                    {
                        "candidate_id": "C1",
                        "title": "Official Ebola data table",
                        "url": "https://example.org/ebola/table",
                        "domain": "example.org",
                        "evidence_summary": "Safe summary only.",
                        "source_type": "structured_table",
                        "confidence": 0.8,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    sources = load_source_pack(path)

    assert len(sources) == 1
    assert sources[0].source_id == "SRC1"
    assert sources[0].domain == "example.org"
    assert sources[0].full_text_saved is False


def test_source_pack_builder_falls_back_to_mock() -> None:
    request = build_disease_time_query_request(
        disease="Ebola",
        country_or_region="African Region",
        date_start="2026-05-01",
        date_end="2026-07-01",
    )

    assert build_source_pack(request)
    assert mock_source_pack(request)[0].source_id == "SRC1"
