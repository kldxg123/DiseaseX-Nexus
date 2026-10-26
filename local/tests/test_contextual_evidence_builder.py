from __future__ import annotations

from deepseek_web_search_agent.contextual_evidence_builder import build_contextual_evidence_pack
from deepseek_web_search_agent.structured_table_types import SourceEvidenceItem


def test_contextual_evidence_builder_links_report_date_to_nearby_metric() -> None:
    source = SourceEvidenceItem(
        source_id="SRC1",
        title_summary="Ebola situation report",
        url_safe_summary="https://example.org/report",
        domain="example.org",
        published_date_hint="2026-05-15",
        paragraph_signal_preview=[{"text": "12 confirmed cases and 3 deaths were reported."}],
    )

    pack = build_contextual_evidence_pack([source])

    assert len(pack.date_anchors) >= 1
    assert len(pack.metric_anchors) >= 1
    assert len(pack.linked_candidates) >= 1
    assert pack.linked_candidates[0].source_id == "SRC1"
    assert pack.linked_candidates[0].date_source == "report_date_fallback"
