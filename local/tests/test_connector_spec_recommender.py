from __future__ import annotations

from live_source_search_orchestrator.candidate_normalizer import normalize_search_result
from live_source_search_orchestrator.candidate_scorer import score_candidates
from live_source_search_orchestrator.connector_spec_recommender import recommend_connector_specs
from live_source_search_orchestrator.disease_alias_expander import expand_disease_aliases


def test_connector_spec_recommender_never_auto_enables():
    aliases = expand_disease_aliases("cholera")
    candidate = normalize_search_result(
        {
            "title": "Cholera CSV surveillance dataset",
            "url": "https://catalog.data.gov/dataset/cholera-surveillance.csv",
            "snippet": "United States cholera cases csv dataset surveillance",
            "rank": 1,
        },
        provider="data_gov",
        country="United States",
        aliases=aliases,
    )
    specs = recommend_connector_specs(score_candidates([candidate], aliases=aliases))
    assert specs
    assert specs[0]["requires_human_review"] is True
    assert specs[0]["auto_enable"] is False
    assert specs[0]["registry_status"] == "pending_review"
