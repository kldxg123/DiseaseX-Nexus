from __future__ import annotations

import pytest

from crawler_agent.generic_parsers import parse_by_strategy


def test_parse_by_strategy_dispatches_known_strategies() -> None:
    csv_result = parse_by_strategy(
        "csv_dataset",
        "week,region,cases\n2025-W01,US,10\n",
        source_name="CSV Fixture",
        url="https://local.invalid/csv",
        country="United States",
        disease="influenza",
    )
    json_result = parse_by_strategy(
        "official_dataset",
        '{"records":[{"epiweek":"2025-W01","cases":10}]}',
        source_name="JSON Fixture",
        url="https://local.invalid/json",
        country="United States",
        disease="influenza",
    )
    dashboard_result = parse_by_strategy(
        "dashboard_data_source",
        '{"series":[{"epiweek":"2025-W02","cases":11}]}',
        source_name="Dashboard Fixture",
        url="https://local.invalid/dashboard",
        country="United States",
        disease="influenza",
    )

    assert len(csv_result.records) == 1
    assert len(json_result.records) == 1
    assert dashboard_result.parser_strategy == "dashboard_data_source"
    assert dashboard_result.records[0].parser_strategy == "dashboard_data_source"


def test_parse_by_strategy_rejects_unknown_strategy() -> None:
    with pytest.raises(ValueError, match="Unknown parser strategy"):
        parse_by_strategy(
            "unknown_strategy",
            "",
            source_name="Fixture",
            url="https://local.invalid/unknown",
        )
