from __future__ import annotations

import json

from live_source_search_orchestrator.types import LiveSourceSearchRequest, ProviderPlan, ReviewQueueItem


def test_live_source_search_types_are_json_serializable():
    request = LiveSourceSearchRequest(country="United States", disease="cholera")
    plan = ProviderPlan(providers_requested="auto", providers_planned=["data_gov"], max_total_requests=1)
    item = ReviewQueueItem(
        candidate_id="abc",
        title="Cholera dataset",
        domain="catalog.data.gov",
        source_score=0.8,
        source_type="csv_dataset",
        parser_strategy="csv_dataset",
        evidence_summary="provider=data_gov",
        recommended_action="controlled_acquisition_candidate",
        risk_flags=[],
        requires_human_review=True,
        suggested_next_step="review",
        registry_status="pending_review",
    )
    rendered = json.dumps({"request": request.to_dict(), "plan": plan.to_dict(), "item": item.to_dict()})
    assert "cholera" in rendered
    assert "pending_review" in rendered
