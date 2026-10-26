from __future__ import annotations

import json
from pathlib import Path

from crawler_agent.generic_parsers import parse_html_table

FIXTURES = Path("tests/fixtures")


def test_parse_html_table_reads_influenza_table_fixture() -> None:
    result = parse_html_table(
        (FIXTURES / "cdc_fluview_table.html").read_text(encoding="utf-8"),
        source_name="CDC FluView",
        url="https://local.invalid/fluview/table-fixture",
        country="United States",
        disease="influenza",
    )

    assert result.parser_strategy == "html_table"
    assert len(result.records) == 2
    assert result.records[0].structured_fields["iso_week"] == "2025-W05"
    assert result.records[0].structured_fields["cases"] == 2210
    assert result.records[0].metadata["table_number"] == 1
    rendered_metadata = json.dumps(result.records[0].metadata, ensure_ascii=False).lower()
    assert "<html" not in rendered_metadata


def test_parse_html_table_reads_norovirus_table_fixture() -> None:
    payload = """
    <html><body><table>
      <tr><th>Week</th><th>State</th><th>Total Outbreaks</th></tr>
      <tr><td>2026-W03</td><td>Fixture State</td><td>7</td></tr>
    </table></body></html>
    """
    result = parse_html_table(
        payload,
        source_name="NoroSTAT-like Table",
        url="https://local.invalid/norostat/table-fixture",
        country="United States",
        disease="norovirus",
    )

    assert len(result.records) == 1
    structured = result.records[0].structured_fields
    assert structured["disease"] == "norovirus"
    assert structured["outbreaks_count"] == 7
