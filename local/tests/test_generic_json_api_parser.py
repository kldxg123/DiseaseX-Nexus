from __future__ import annotations

import json
from pathlib import Path

from crawler_agent.generic_parsers import parse_json_api_response

FIXTURES = Path("tests/fixtures")


def test_parse_json_api_response_reads_records_fixture() -> None:
    result = parse_json_api_response(
        (FIXTURES / "influenza_json_api_sample.json").read_text(encoding="utf-8"),
        source_name="CDC FluView JSON API Fixture",
        url="https://local.invalid/fluview/json-api-fixture",
        country="United States",
        disease="influenza",
    )

    assert result.parser_strategy == "json_api"
    assert len(result.records) == 12
    assert result.records[0].structured_fields["iso_week"] == "2025-W01"
    assert result.records[0].structured_fields["tests"] == 1200
    assert any("no recognized week" in warning for warning in result.warnings)
    rendered_metadata = json.dumps(result.records[0].metadata, ensure_ascii=False).lower()
    assert "password" not in rendered_metadata
    assert "token" not in rendered_metadata


def test_parse_json_api_response_supports_records_path() -> None:
    payload = {"data": {"records": [{"epiweek": "2025-W12", "region": "US", "cases": 9}]}}
    result = parse_json_api_response(
        json.dumps(payload),
        source_name="Nested API Fixture",
        url="https://local.invalid/nested-json",
        country="United States",
        disease="influenza",
        records_path="data.records",
    )

    assert len(result.records) == 1
    assert result.records[0].metadata["records_path"] == "data.records"
    assert result.records[0].structured_fields["cases"] == 9
