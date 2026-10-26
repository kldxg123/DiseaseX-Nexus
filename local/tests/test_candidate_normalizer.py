from __future__ import annotations

from live_source_search_orchestrator.candidate_normalizer import normalize_search_result
from live_source_search_orchestrator.disease_alias_expander import expand_disease_aliases


def test_candidate_normalizer_truncates_and_extracts_terms():
    candidate = normalize_search_result(
        {
            "title": "Cholera CSV surveillance dataset",
            "url": "https://catalog.data.gov/dataset/cholera-surveillance?x=1",
            "snippet": "United States cholera surveillance dataset " * 20,
            "source_name": "Data.gov",
            "rank": 1,
        },
        provider="data_gov",
        country="United States",
        aliases=expand_disease_aliases("cholera"),
    )
    assert candidate is not None
    assert candidate.normalized_url == "https://catalog.data.gov/dataset/cholera-surveillance"
    assert candidate.domain == "catalog.data.gov"
    assert "cholera" in [item.lower() for item in candidate.disease_mentions]
    assert len(candidate.snippet_summary) <= 235


def test_candidate_normalizer_rejects_invalid_url():
    assert (
        normalize_search_result(
            {"title": "bad", "url": "not-a-url"},
            provider="data_gov",
            country="United States",
            aliases=expand_disease_aliases("cholera"),
        )
        is None
    )
