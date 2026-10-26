from __future__ import annotations

from deepseek_web_search_agent.contextual_evidence_builder import build_contextual_evidence_pack
from deepseek_web_search_agent.structured_table_types import SourceEvidenceItem


def test_cross_paragraph_linking_stays_within_same_source() -> None:
    dated_source = SourceEvidenceItem(
        source_id="SRC1",
        title_summary="Ebola report 15 May 2026",
        url_safe_summary="https://example.org/date",
        domain="example.org",
        paragraph_signal_preview=[{"text": "On 15 May 2026 the situation was updated."}],
    )
    metric_source = SourceEvidenceItem(
        source_id="SRC2",
        title_summary="Ebola metrics",
        url_safe_summary="https://example.org/metric",
        domain="example.org",
        paragraph_signal_preview=[{"text": "12 confirmed cases were reported."}],
    )

    pack = build_contextual_evidence_pack([dated_source, metric_source])

    assert pack.date_anchors
    assert pack.metric_anchors
    assert pack.linked_candidates == []
