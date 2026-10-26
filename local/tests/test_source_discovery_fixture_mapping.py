from __future__ import annotations

from pathlib import Path

import pytest

from source_discovery_agent.fixture_mapping import (
    infer_fixture_for_parser_strategy,
    supported_fixture_strategies,
)


def test_fixture_mapping_returns_local_fixture_for_each_strategy() -> None:
    assert supported_fixture_strategies(disease="influenza") == [
        "csv_dataset",
        "html_table",
        "json_api",
        "official_report_page",
    ]
    for strategy in supported_fixture_strategies(disease="influenza"):
        mapping = infer_fixture_for_parser_strategy(
            parser_strategy=strategy,
            disease="influenza",
        )
        fixture_path = Path(mapping["fixture_path"])
        assert fixture_path.exists()
        assert not fixture_path.is_absolute()
        assert mapping["fixture_url"].startswith("https://fixture.invalid/")
        assert mapping["source_type"]


def test_fixture_mapping_rejects_unknown_strategy() -> None:
    with pytest.raises(ValueError, match="unsupported parser strategy"):
        infer_fixture_for_parser_strategy(parser_strategy="unknown", disease="influenza")
