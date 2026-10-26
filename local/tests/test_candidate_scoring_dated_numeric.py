from __future__ import annotations

from live_source_search_orchestrator.candidate_normalizer import normalize_search_result
from live_source_search_orchestrator.candidate_scorer import score_candidate
from live_source_search_orchestrator.disease_alias_expander import expand_disease_aliases


def test_candidate_scoring_exposes_dated_numeric_and_situation_report_scores() -> None:
    aliases = expand_disease_aliases("Ebola")
    candidate = normalize_search_result(
        {
            "title": "WHO AFRO Ebola weekly situation report table",
            "url": "https://www.afro.who.int/ebola/situation-report",
            "snippet": "African Region confirmed cases deaths suspected cases and contacts under monitoring by week.",
            "rank": 1,
        },
        provider="tavily",
        country="African Region",
        aliases=aliases,
    )

    scored = score_candidate(candidate, aliases=aliases)

    assert scored.dated_numeric_likelihood_score > 0
    assert scored.table_or_dataset_likelihood_score > 0
    assert scored.situation_report_score > 0
    assert scored.feature_ready_evidence_score > 0
    assert scored.registry_status == "pending_review"
