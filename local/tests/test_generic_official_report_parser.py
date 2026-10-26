from __future__ import annotations

import json
from pathlib import Path

from crawler_agent.generic_parsers import parse_official_report

FIXTURES = Path("tests/fixtures")


def test_parse_official_report_reads_influenza_report_fixture() -> None:
    result = parse_official_report(
        (FIXTURES / "cdc_fluview_report.html").read_text(encoding="utf-8"),
        source_name="CDC FluView",
        url="https://local.invalid/fluview/report-fixture",
        country="United States",
        disease="influenza",
    )

    assert result.parser_strategy == "official_report_page"
    assert len(result.records) == 1
    record = result.records[0]
    assert "Weekly U.S. Influenza Surveillance Report" in (record.title or "")
    assert record.structured_fields["event_date"] == "2025-02-14"
    assert record.structured_fields["cases"] == 2450
    assert record.structured_fields["confirmed_cases"] == 2100
    assert record.structured_fields["tests"] == 64000
    rendered_metadata = json.dumps(record.metadata, ensure_ascii=False).lower()
    assert "<html" not in rendered_metadata
    assert "raw_text" not in rendered_metadata
