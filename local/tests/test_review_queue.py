from __future__ import annotations

from live_source_search_orchestrator.candidate_normalizer import normalize_search_result
from live_source_search_orchestrator.candidate_scorer import score_candidates
from live_source_search_orchestrator.disease_alias_expander import expand_disease_aliases
from live_source_search_orchestrator.review_queue import build_review_queue, review_queue_to_markdown


def test_review_queue_contains_manual_review_fields():
    aliases = expand_disease_aliases("cholera")
    candidate = normalize_search_result(
        {
            "title": "Cholera dashboard",
            "url": "https://www.cdc.gov/cholera/dashboard",
            "snippet": "United States cholera dashboard surveillance",
            "rank": 1,
        },
        provider="official_directory",
        country="United States",
        aliases=aliases,
    )
    queue = build_review_queue(score_candidates([candidate], aliases=aliases))
    assert queue[0].requires_human_review is True
    assert queue[0].registry_status == "pending_review"
    assert "metadata_check_required" in queue[0].risk_flags
    assert "Cholera dashboard" in review_queue_to_markdown(queue)
