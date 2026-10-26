from __future__ import annotations

from live_source_search_orchestrator.candidate_normalizer import normalize_search_result
from live_source_search_orchestrator.candidate_scorer import score_candidate, score_candidates
from live_source_search_orchestrator.disease_alias_expander import expand_disease_aliases


def test_candidate_scorer_ranks_official_dataset_above_keyword_news():
    aliases = expand_disease_aliases("cholera")
    official = normalize_search_result(
        {
            "title": "Cholera surveillance CSV dataset",
            "url": "https://catalog.data.gov/dataset/cholera-surveillance",
            "snippet": "United States cholera cases csv dataset surveillance",
            "rank": 1,
        },
        provider="data_gov",
        country="United States",
        aliases=aliases,
    )
    news = normalize_search_result(
        {
            "title": "Cholera outbreak cholera cholera cholera",
            "url": "https://healthnews.example/cholera",
            "snippet": "United States cholera outbreak report with many disease keywords",
            "rank": 1,
        },
        provider="gdelt",
        country="United States",
        aliases=aliases,
    )
    ranked = score_candidates([official, news], aliases=aliases)
    assert ranked[0].domain == "catalog.data.gov"
    assert score_candidate(news, aliases=aliases).total_source_score <= 0.49
