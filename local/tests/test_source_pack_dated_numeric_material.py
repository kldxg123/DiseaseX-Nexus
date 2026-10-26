from __future__ import annotations

import json

from deepseek_web_search_agent.query_request import build_disease_time_query_request
from deepseek_web_search_agent.source_pack_builder import build_source_pack, load_source_pack, source_pack_readiness


def test_source_pack_promotes_dated_numeric_paragraph_material(tmp_path) -> None:
    path = tmp_path / "page_read_summary.json"
    path.write_text(
        json.dumps(
            {
                "page_reads": [
                    {
                        "url": "https://example.org/ebola-report",
                        "domain": "example.org",
                        "title": "Ebola situation report",
                        "text_summary": "Safe summary only.",
                        "source_type": "official_report",
                        "hints": {
                            "paragraph_signal_preview": [
                                {
                                    "text": "On 15 May 2026, 12 confirmed cases and 3 deaths were reported.",
                                    "has_date_or_week": True,
                                    "has_numeric_metric": True,
                                    "case_signal": True,
                                    "death_signal": True,
                                    "contact_signal": False,
                                }
                            ],
                            "paragraph_signal_preview_count": 1,
                            "dated_numeric_signal_count": 1,
                            "date_terms_count": 1,
                            "numeric_terms_count": 2,
                            "situation_report_hint": True,
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    sources = load_source_pack(path)
    readiness = source_pack_readiness(sources)

    assert sources[0].material_strength == "situation_report_preview"
    assert sources[0].extraction_readiness == "ready_for_table_extraction"
    assert readiness["dated_numeric_signal_count"] == 1
    assert readiness["paragraph_signal_preview_count"] == 1
    assert readiness["has_structured_numeric_material"] is True


def test_build_source_pack_selects_extractable_material_before_search_summaries(tmp_path) -> None:
    path = tmp_path / "structured_source_pack.json"
    path.write_text(
        json.dumps(
            {
                "sources": [
                    {"title": "Search summary 1", "url": "https://example.org/1"},
                    {"title": "Search summary 2", "url": "https://example.org/2"},
                    {"title": "Search summary 3", "url": "https://example.org/3"},
                    {
                        "title": "Ebola situation report with dated numeric paragraph",
                        "url": "https://example.org/report",
                        "hints": {
                            "paragraph_signal_preview": [
                                {
                                    "text": "On 15 May 2026, 12 confirmed cases were reported.",
                                    "has_date_or_week": True,
                                    "has_numeric_metric": True,
                                    "case_signal": True,
                                }
                            ],
                            "paragraph_signal_preview_count": 1,
                            "dated_numeric_signal_count": 1,
                            "date_terms_count": 1,
                            "numeric_terms_count": 1,
                        },
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    request = build_disease_time_query_request(
        disease="Ebola",
        country_or_region="African Region",
        date_start="2026-05-01",
        date_end="2026-07-01",
        max_sources=3,
    )

    sources = build_source_pack(request, source_pack_path=str(path))

    assert sources[0].paragraph_signal_preview_count == 1
    assert sources[0].extraction_readiness == "ready_for_table_extraction"


def test_source_pack_marks_split_date_and_numeric_paragraphs_as_weak(tmp_path) -> None:
    path = tmp_path / "page_read_summary.json"
    path.write_text(
        json.dumps(
            {
                "page_reads": [
                    {
                        "url": "https://example.org/report",
                        "domain": "example.org",
                        "title": "Ebola report",
                        "hints": {
                            "paragraph_signal_preview": [
                                {"text": "Week 21 update.", "has_date_or_week": True, "has_numeric_metric": False},
                                {"text": "12 confirmed cases were reported.", "has_date_or_week": False, "has_numeric_metric": True},
                            ],
                            "paragraph_signal_preview_count": 2,
                            "dated_numeric_signal_count": 0,
                            "date_terms_count": 1,
                            "numeric_terms_count": 1,
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    source = load_source_pack(path)[0]

    assert source.extraction_readiness == "weak_but_usable"
    assert "needs_structured_document_or_dataset" in source.extraction_blockers
