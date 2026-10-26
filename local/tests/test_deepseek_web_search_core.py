from __future__ import annotations

from common.db import get_session, init_db
from common.models import (
    DeepSeekWebSearchRun,
    DiseaseCountryInformationRecord,
)
from deepseek_web_search_agent import DeepSeekWebSearchRequest, run_deepseek_web_search
from deepseek_web_search_agent.query_planner import deterministic_query_plan
from deepseek_web_search_agent.result_ranker import rank_results
from deepseek_web_search_agent.structured_extractor import deterministic_record_from_result
from deepseek_web_search_agent.types import GeneralWebSearchRequest, GeneralWebSearchResult
from deepseek_web_search_agent.web_search_providers import MockSearchProvider


def _database_url(tmp_path) -> str:
    return f"sqlite:///{(tmp_path / 'deepseek-core.db').as_posix()}"


def test_request_bounds_and_query_plan_are_safe() -> None:
    request = DeepSeekWebSearchRequest(
        disease="influenza",
        country="United States",
        max_search_queries=99,
        max_search_results_per_query=99,
        max_pages_to_read=99,
        max_total_http_requests=99,
    ).validated()

    assert request.max_search_queries == 6
    assert request.max_search_results_per_query == 8
    assert request.max_pages_to_read == 8
    assert request.max_total_http_requests == 16
    plan = deterministic_query_plan(request)
    assert plan.disease == "influenza"
    assert "flu" in plan.disease_aliases
    assert len(plan.search_queries) <= 8


def test_mock_provider_ranker_and_extractor_create_feature_ready_record() -> None:
    request = DeepSeekWebSearchRequest(
        disease="influenza",
        country="United States",
        max_search_results_per_query=3,
    ).validated()
    plan = deterministic_query_plan(request)
    results = MockSearchProvider().search(
        GeneralWebSearchRequest(
            query="United States influenza weekly cases",
            disease="influenza",
            country="United States",
            max_results=3,
            allow_network=False,
        )
    )
    extra = GeneralWebSearchResult(
        query="United States influenza weekly cases",
        rank=4,
        title="United States influenza report 2026-W10",
        url="https://health.example.gov/influenza/report",
        domain="health.example.gov",
        snippet="Public health report: 12 cases in United States during 2026-W10.",
        provider="mock",
    )
    ranked = rank_results([*results, extra], plan)

    assert ranked[0].total_score >= ranked[-1].total_score
    record = deterministic_record_from_result(ranked[0], request=request)
    assert record.disease == "influenza"
    assert record.requires_human_review is True
    record_with_metric = deterministic_record_from_result(rank_results([extra], plan)[0], request=request)
    assert record_with_metric.iso_week == "2026-W10"
    assert record_with_metric.metric_name == "cases"
    assert record_with_metric.metric_value == 12
    assert record_with_metric.feature_ready_potential is True


def test_mock_orchestrator_persists_runs_and_records(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    with get_session(database_url) as session:
        summary = run_deepseek_web_search(
            DeepSeekWebSearchRequest(
                disease="influenza",
                country="United States",
                search_backend="mock",
                output_dir=str(tmp_path / "reports"),
                max_search_queries=2,
                max_search_results_per_query=3,
                max_pages_to_read=0,
                max_total_http_requests=4,
            ),
            session=session,
        )

        assert summary.status == "ok"
        assert summary.search_request_count == 2
        assert summary.deepseek_call_count == 0
        assert summary.page_read_request_count == 0
        assert summary.records_persisted > 0
        assert session.query(DeepSeekWebSearchRun).count() == 1
        assert session.query(DiseaseCountryInformationRecord).count() == summary.records_persisted
        assert "summary_json" in summary.paths


def test_storage_tables_exist_with_init_db(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    engine = init_db(database_url)
    with engine.connect() as connection:
        table_names = set(engine.dialect.get_table_names(connection))
    assert "deepseek_web_search_runs" in table_names
    assert "deepseek_web_search_results" in table_names
    assert "disease_country_information_records" in table_names
