from __future__ import annotations

from dataclasses import replace

from live_source_search_orchestrator.candidate_deduper import dedupe_candidates
from live_source_search_orchestrator.candidate_normalizer import normalize_search_result
from live_source_search_orchestrator.disease_alias_expander import expand_disease_aliases


def test_candidate_deduper_merges_duplicate_urls_and_providers():
    aliases = expand_disease_aliases("cholera")
    first = normalize_search_result(
        {
            "title": "Cholera surveillance dataset",
            "url": "https://catalog.data.gov/dataset/cholera",
            "snippet": "cholera United States dataset",
            "rank": 1,
        },
        provider="data_gov",
        country="United States",
        aliases=aliases,
    )
    second = replace(first, provider="gdelt", merged_from_providers=["gdelt"])
    deduped, summary = dedupe_candidates([first, second])
    assert len(deduped) == 1
    assert summary["merged_count"] == 1
    assert set(deduped[0].merged_from_providers) == {"data_gov", "gdelt"}
