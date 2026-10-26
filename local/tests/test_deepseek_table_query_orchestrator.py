from __future__ import annotations

from deepseek_web_search_agent.table_query_orchestrator import build_table_query_source_pack


def test_table_query_orchestrator_builds_mock_source_pack_without_network(tmp_path) -> None:
    result = build_table_query_source_pack(
        disease="Ebola",
        country_or_region="African Region",
        date_start="2026-05-01",
        date_end="2026-07-01",
        output_dir=str(tmp_path / "source-search"),
    )

    assert result.query_request.disease == "Ebola"
    assert len(result.sources) >= 1
    assert result.network_accessed is False
    assert result.provider_request_count_total == 0
    assert result.candidate_acquisition_request_count == 0
    assert result.source_search_summary["live_source_search_executed"] is False


def test_table_query_orchestrator_blocks_live_without_review_flags(tmp_path) -> None:
    result = build_table_query_source_pack(
        disease="Ebola",
        country_or_region="African Region",
        date_start="2026-05-01",
        date_end="2026-07-01",
        allow_live_source_search=True,
        allow_network=False,
        output_dir=str(tmp_path / "source-search"),
    )

    assert result.network_accessed is False
    assert "live_source_search_flags_required" in result.warnings
    assert len(result.sources) >= 1
