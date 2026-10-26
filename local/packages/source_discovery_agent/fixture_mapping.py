from __future__ import annotations

from pathlib import Path
from typing import Any

FIXTURE_ROOT = Path("tests/fixtures")

INFLUENZA_FIXTURES: dict[str, dict[str, Any]] = {
    "csv_dataset": {
        "fixture_path": "tests/fixtures/cdc_fluview_ili.csv",
        "fixture_url": "https://fixture.invalid/fluview/csv",
        "source_name": "CDC FluView CSV Fixture",
        "source_type": "official_dataset",
        "field_mapping": None,
        "records_path": None,
    },
    "html_table": {
        "fixture_path": "tests/fixtures/cdc_fluview_table.html",
        "fixture_url": "https://fixture.invalid/fluview/html-table",
        "source_name": "CDC FluView HTML Table Fixture",
        "source_type": "official_dataset",
        "field_mapping": None,
        "records_path": None,
    },
    "json_api": {
        "fixture_path": "tests/fixtures/influenza_json_api_sample.json",
        "fixture_url": "https://fixture.invalid/fluview/json-api",
        "source_name": "CDC FluView JSON API Fixture",
        "source_type": "official_dataset",
        "field_mapping": None,
        "records_path": "records",
    },
    "official_report_page": {
        "fixture_path": "tests/fixtures/cdc_fluview_report.html",
        "fixture_url": "https://fixture.invalid/fluview/official-report",
        "source_name": "CDC FluView Official Report Fixture",
        "source_type": "official_report_page",
        "field_mapping": None,
        "records_path": None,
    },
}


def infer_fixture_for_parser_strategy(
    *,
    parser_strategy: str,
    disease: str,
    source_name: str | None = None,
) -> dict:
    strategy = str(parser_strategy or "").strip().lower()
    normalized_disease = str(disease or "").strip().lower()
    if normalized_disease != "influenza":
        raise ValueError(f"No local bridge fixture configured for disease: {disease}.")
    try:
        mapping = dict(INFLUENZA_FIXTURES[strategy])
    except KeyError as exc:
        raise ValueError(f"Unknown or unsupported parser strategy for fixture bridge: {parser_strategy}.") from exc
    if source_name:
        mapping["source_name"] = str(source_name)
    fixture_path = Path(mapping["fixture_path"])
    if fixture_path.is_absolute() or ".." in fixture_path.parts:
        raise ValueError("Fixture path must be a relative local fixture path.")
    return mapping


def supported_fixture_strategies(*, disease: str = "influenza") -> list[str]:
    if str(disease or "").strip().lower() != "influenza":
        return []
    return sorted(INFLUENZA_FIXTURES)
