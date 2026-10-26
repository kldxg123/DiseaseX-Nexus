from __future__ import annotations

from source_discovery_agent import evaluate_search_result, load_search_results_fixture
from source_discovery_agent.source_evaluator import infer_source_type, rank_source_candidates


FIXTURE = "tests/fixtures/source_discovery_norovirus_search_results.json"


def test_evaluator_scores_cdc_candidate_high() -> None:
    cdc = load_search_results_fixture(FIXTURE)[0]
    candidate = evaluate_search_result(cdc, country="United States", disease="norovirus")

    assert candidate is not None
    assert candidate.domain == "www.cdc.gov"
    assert candidate.source_score > 0.7
    assert candidate.source_type == "official_dataset"
    assert candidate.requires_human_review is True


def test_evaluator_scores_low_relevance_result_low() -> None:
    result = [item for item in load_search_results_fixture(FIXTURE) if item.source_name == "Example Weather"][0]
    candidate = evaluate_search_result(result, country="United States", disease="norovirus")

    assert candidate is not None
    assert candidate.source_score < 0.35
    assert candidate.relevance_score <= 0.1


def test_rank_source_candidates_orders_by_score() -> None:
    candidates = [
        evaluate_search_result(result, country="United States", disease="norovirus")
        for result in load_search_results_fixture(FIXTURE)
    ]
    ranked = rank_source_candidates([candidate for candidate in candidates if candidate is not None])

    assert ranked[0].source_score >= ranked[-1].source_score
    assert infer_source_type(load_search_results_fixture(FIXTURE)[6]) == "news"
