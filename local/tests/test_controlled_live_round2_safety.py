from __future__ import annotations

import json
from pathlib import Path

import httpx

from scripts.run_controlled_live_pilot import run_controlled_live_pilot_live
from tests.test_controlled_live_round2_mock import LIVE_URL, _six_week_html

FORBIDDEN = (
    "服务器信息",
    "ssh_key_path",
    "password",
    "token",
    "secret",
    "private key",
    "<html",
    "raw_text",
    "feature_json",
    "metadata_json",
    "provenance_json",
    "lesson_json",
)


def test_round2_rejects_unapproved_url_without_network(tmp_path) -> None:
    summary = run_controlled_live_pilot_live(
        source_key="cdc_norostat",
        live_url="https://www.cdc.gov/not-the-approved-url",
        allow_network=True,
        terms_reviewed=True,
        manual_reviewed=True,
        robots_allowed=True,
        max_requests=1,
        output_dir=str(tmp_path),
        round_label="round2",
    )

    assert summary["status"] == "failed"
    assert summary["network_accessed"] is False
    assert "single allowed" in summary["warnings"][0]


def test_round2_rejects_more_than_one_request_without_network(tmp_path) -> None:
    summary = run_controlled_live_pilot_live(
        source_key="cdc_norostat",
        live_url=LIVE_URL,
        allow_network=True,
        terms_reviewed=True,
        manual_reviewed=True,
        robots_allowed=True,
        max_requests=2,
        output_dir=str(tmp_path),
        round_label="round2",
    )

    assert summary["status"] == "failed"
    assert summary["network_accessed"] is False
    assert "exactly 1" in summary["warnings"][0]


def test_round2_outputs_do_not_expose_raw_payload_markers(tmp_path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
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

    rendered_summary = json.dumps(summary, ensure_ascii=False).lower()
    for marker in FORBIDDEN:
        assert marker.lower() not in rendered_summary

    run_dir = Path(summary["output_dir"])
    report_paths = [
        run_dir / "round2_live_fetch_summary.json",
        run_dir / "round2_live_fetch_summary.md",
        run_dir / "crawler_optimizer_report.json",
        run_dir / "comparison_round1_reprocess_round2.json",
        run_dir / "rollback_commands.md",
    ]
    for path in report_paths:
        assert path.exists()
        payload = path.read_text(encoding="utf-8").lower()
        for marker in FORBIDDEN:
            assert marker.lower() not in payload
