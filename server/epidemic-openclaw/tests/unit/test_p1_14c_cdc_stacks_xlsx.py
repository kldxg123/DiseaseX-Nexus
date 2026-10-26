from __future__ import annotations

import zipfile
from io import BytesIO

from epidemic_core.ebola.fetcher import FetchResult
from epidemic_core.p1_12.discovery import WEST_AFRICA_CDC_STACKS_SPECS, CandidateDiscoveryService


def _xlsx_fixture() -> bytes:
    payload = BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheets><sheet name="Guinea" sheetId="1" r:id="rId1" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/></sheets>
</workbook>""",
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <si><t>WHO report date</t></si>
  <si><t>Total Cases, Guinea</t></si>
  <si><t>Total Deaths, Guinea</t></si>
</sst>""",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c></row>
    <row r="7"><c r="A7"><v>41699</v></c><c r="B7"><v>86</v></c><c r="C7"><v>59</v></c></row>
  </sheetData>
</worksheet>""",
        )
    return payload.getvalue()


def test_cdc_stacks_xlsx_uses_reported_total_metric_semantics_and_separate_lineage() -> None:
    payload = _xlsx_fixture()
    result = FetchResult(
        final_url="https://stacks.cdc.gov/view/cdc/37575/cdc_37575_DS2.xlsx",
        status=200,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        payload=payload,
        sha256="a" * 64,
        etag=None,
        last_modified=None,
        redirect_hashes=(),
    )
    rows = CandidateDiscoveryService._cdc_stacks_west_africa_rows(
        result, WEST_AFRICA_CDC_STACKS_SPECS[0]
    )

    assert [row.metric_code for row in rows] == ["cases_cumulative", "deaths_cumulative"]
    assert {row.case_definition for row in rows} == {"combined_or_unspecified"}
    assert {row.report_date.isoformat() for row in rows} == {"2014-03-01"}
    assert rows[0].source_row_locator == "xlsx://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/Guinea!A7,B7"
    assert rows[1].source_row_locator == "xlsx://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/Guinea!A7,C7"
    assert all("REPORT_DATE_NOT_ONSET_OR_EVENT_DATE" in row.quality_flags for row in rows)
