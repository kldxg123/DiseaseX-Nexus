from __future__ import annotations

from pathlib import Path

from crawler_agent.generic_parsers import parse_csv_dataset

FIXTURES = Path("tests/fixtures")


def test_parse_csv_dataset_reads_influenza_fixture() -> None:
    result = parse_csv_dataset(
        (FIXTURES / "cdc_fluview_ili.csv").read_text(encoding="utf-8"),
        source_name="CDC FluView",
        url="https://local.invalid/fluview/csv-fixture",
        country="United States",
        disease="influenza",
    )

    assert result.parser_strategy == "csv_dataset"
    assert len(result.records) == 4
    first = result.records[0]
    assert first.structured_fields["iso_week"] == "2025-W01"
    assert first.structured_fields["cases"] == 1520
    assert first.structured_fields["tests"] == 48000
    assert first.structured_fields["confirmed_cases"] == 4200
    assert first.structured_fields["positivity_rate"] == 3.2
    assert first.metadata["field_count"] >= 6


def test_parse_csv_dataset_supports_norostat_like_mapping() -> None:
    payload = (
        "week,state,suspected outbreaks,confirmed outbreaks,total outbreaks\n"
        "2026-W02,Example State,2,3,5\n"
    )
    result = parse_csv_dataset(
        payload,
        source_name="NoroSTAT-like Fixture",
        url="https://local.invalid/norostat/csv-fixture",
        country="United States",
        disease="norovirus",
    )

    structured = result.records[0].structured_fields
    assert structured["disease"] == "norovirus"
    assert structured["admin1"] == "Example State"
    assert structured["outbreaks_count"] == 5
    assert structured["confirmed_outbreaks"] == 3
    assert result.warnings == []
