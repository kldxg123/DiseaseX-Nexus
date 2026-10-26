from __future__ import annotations

from live_source_search_orchestrator.candidate_normalizer import normalize_search_result
from live_source_search_orchestrator.candidate_quality_gate import apply_quality_gate
from live_source_search_orchestrator.candidate_scorer import score_candidates
from live_source_search_orchestrator.disease_alias_expander import expand_disease_aliases
from live_source_search_orchestrator.review_queue import build_review_queue


def test_refined_review_queue_contains_quality_fields_and_reorders():
    aliases = expand_disease_aliases("measles")
    rows = [
        {"title": "CDC jobs", "url": "https://jobs.cdc.gov/careers", "snippet": "job hiring"},
        {
            "title": "Measles weekly surveillance data",
            "url": "https://www.cdc.gov/measles/data",
            "snippet": "United States cases table dashboard weekly report",
        },
    ]
    candidates = [
        normalize_search_result(row, provider="official_directory", country="United States", aliases=aliases)
        for row in rows
    ]
    scored = score_candidates([candidate for candidate in candidates if candidate is not None], aliases=aliases)
    gated, _ = apply_quality_gate(scored)
    refined = [candidate for candidate in gated if candidate.quality_gate_status != "rejected"]
    queue = build_review_queue(refined)

    assert len(queue) == 1
    assert queue[0].quality_gate_status != "rejected"
    assert queue[0].refined_score > 0
