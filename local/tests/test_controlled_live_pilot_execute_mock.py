from __future__ import annotations

import json
from pathlib import Path

import httpx

from scripts.run_controlled_live_pilot import run_controlled_live_pilot_live

LIVE_URL = "https://www.cdc.gov/norovirus/php/reporting/norostat-data-table.html"


def test_controlled_live_pilot_execute_with_mock_transport_writes_safe_outputs(tmp_path) -> None:
    html = Path("tests/fixtures/cdc_norostat_live_mock.html").read_text(encoding="utf-8")
    requests_seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    summary = run_controlled_live_pilot_live(
        source_key="cdc_norostat",
        live_url=LIVE_URL,
        allow_network=True,
        terms_reviewed=True,
        manual_reviewed=True,
        robots_allowed=True,
        max_requests=1,
        output_dir=str(tmp_path),
        transport=httpx.MockTransport(handler),
    )

    assert summary["status"] == "ok"
    assert requests_seen == [LIVE_URL]
    assert summary["guardrail_allowed"] is True
    assert summary["http_status_code"] == 200
    assert summary["connector_documents_parsed"] == 3
    assert summary["raw_documents_saved"] == 3
    assert summary["production_prediction_started"] is False
    assert Path(summary["data_quality_report"]["json"]).exists()
    assert Path(summary["data_quality_report"]["markdown"]).exists()
    assert Path(summary["crawler_optimizer"]["json"]).exists()
    assert Path(summary["rollback_commands"]).exists()
    rendered = json.dumps(summary, ensure_ascii=False).lower()
    assert "<html" not in rendered
    assert "raw_text" not in rendered
    assert "feature_json" not in rendered
    assert "lesson_json" not in rendered
    assert "provenance_json" not in rendered
    assert "metadata_json" not in rendered
    assert "token" not in rendered
    assert "secret" not in rendered
    assert "password" not in rendered


def test_controlled_live_pilot_http_failure_still_writes_safe_summary(tmp_path) -> None:
    requests_seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(str(request.url))
        return httpx.Response(503, headers={"content-type": "text/html"}, text="<html>temporary failure</html>")

    summary = run_controlled_live_pilot_live(
        source_key="cdc_norostat",
        live_url=LIVE_URL,
        allow_network=True,
        terms_reviewed=True,
        manual_reviewed=True,
        robots_allowed=True,
        max_requests=1,
        output_dir=str(tmp_path),
        transport=httpx.MockTransport(handler),
    )

    assert summary["status"] == "failed"
    assert requests_seen == [LIVE_URL]
    assert summary["http_status_code"] == 503
    assert Path(summary["data_quality_report"]["json"]).exists()
    assert Path(summary["crawler_optimizer"]["json"]).exists()
    assert Path(summary["rollback_commands"]).exists()
    rendered = json.dumps(summary, ensure_ascii=False).lower()
    assert "<html" not in rendered
    assert "temporary failure" not in rendered
