from __future__ import annotations

import json
from pathlib import Path

import httpx

from scripts.run_controlled_live_pilot import run_controlled_live_pilot_live

LIVE_URL = "https://www.cdc.gov/norovirus/php/reporting/norostat-data-table.html"


def _six_week_html() -> str:
    rows = []
    for index in range(1, 7):
        rows.append(
            f"""
            <tr>
              <td>National</td>
              <td>2025-W0{index}</td>
              <td>{index}</td>
              <td>1</td>
              <td>{index + 1}</td>
            </tr>
            """
        )
    return (
        "<!doctype html><html><body><table>"
        "<tr><th>State</th><th>Illness Onset Week</th>"
        "<th>Suspected Outbreaks</th><th>Confirmed Outbreaks</th><th>Total Outbreaks</th></tr>"
        + "\n".join(rows)
        + "</table></body></html>"
    )


def test_round2_mock_live_fetch_generates_features_and_reports(tmp_path) -> None:
    requests_seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "text/html"}, text=_six_week_html())

    summary = run_controlled_live_pilot_live(
        source_key="cdc_norostat",
        live_url=LIVE_URL,
        allow_network=True,
        terms_reviewed=True,
        manual_reviewed=True,
        robots_allowed=True,
        max_requests=1,
        output_dir=str(tmp_path),
        round_label="round2",
        transport=httpx.MockTransport(handler),
    )

    run_dir = Path(summary["output_dir"])
    assert summary["status"] == "ok"
    assert summary["round_label"] == "round2"
    assert requests_seen == [LIVE_URL]
    assert run_dir.name.startswith("cdc_norostat_round2_")
    assert (run_dir / "cdc_norostat_round2.sqlite").exists()
    assert (run_dir / "round2_live_fetch_summary.json").exists()
    assert (run_dir / "round2_live_fetch_summary.md").exists()
    assert summary["connector_documents_parsed"] == 6
    assert summary["raw_documents_saved"] == 6
    assert summary["cleaner"]["events_created"] == 6
    assert summary["features"]["feature_rows_h7"] == 5
    assert summary["features"]["feature_rows_h28"] == 2
    assert summary["crawler_optimizer"]["failure_case_count"] == 0
    assert summary["production_prediction_started"] is False
    rendered = json.dumps(summary, ensure_ascii=False).lower()
    assert "<html" not in rendered
    assert "raw_text" not in rendered
    assert "feature_json" not in rendered
    assert "provenance_json" not in rendered
    assert "lesson_json" not in rendered
