from __future__ import annotations

import json

from deepseek_web_search_agent.contextual_evidence_builder import build_contextual_evidence_pack
from deepseek_web_search_agent.deepseek_table_prompt import build_structured_event_table_prompt, prompt_summary
from deepseek_web_search_agent.query_request import build_disease_time_query_request
from deepseek_web_search_agent.structured_table_types import SourceEvidenceItem


def test_prompt_supports_cross_context_anchor_schema_without_raw_completion() -> None:
    request = build_disease_time_query_request(
        disease="Ebola",
        country_or_region="African Region",
        date_start="2026-05-01",
        date_end="2026-07-01",
    )
    source = SourceEvidenceItem(
        source_id="SRC1",
        title_summary="Ebola report",
        url_safe_summary="https://example.org/report",
        domain="example.org",
        published_date_hint="2026-05-15",
        paragraph_signal_preview=[{"text": "12 confirmed cases were reported."}],
    )
    context = build_contextual_evidence_pack([source]).to_dict()

    payload = json.loads(build_structured_event_table_prompt(request, [source], context))
    summary = prompt_summary(request, [source], context)

    row_schema = payload["output_schema"]["rows"][0]
    assert "date_source" in row_schema
    assert "date_anchor_id" in row_schema
    assert "metric_anchor_id" in row_schema
    assert payload["contextual_evidence_pack"]["counts"]["context_linked_candidate_count"] >= 1
    assert summary["context_linked_candidate_count"] >= 1
    assert "completion" not in payload
