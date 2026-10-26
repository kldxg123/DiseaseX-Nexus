from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx

from scripts.run_controlled_live_source_discovery import run_controlled_live_source_discovery

FIXTURE = Path("tests/fixtures/official_directory_fluview_mock.html")


def test_official_directory_execute_mock_writes_pending_review_sources(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=FIXTURE.read_text(encoding="utf-8"))

    result = run_controlled_live_source_discovery(
        provider="official_directory",
        country="United States",
        disease="influenza",
        execute_live=True,
        allow_network=True,
        terms_reviewed=True,
        manual_reviewed=True,
        directory_url="https://www.cdc.gov/flu/weekly/index.htm",
        max_requests=1,
        output_dir=str(tmp_path / "live_runs"),
        transport=httpx.MockTransport(handler),
    )

    with sqlite3.connect(result["live_discovery_db_path"]) as connection:
        statuses = connection.execute("SELECT status FROM source_registry").fetchall()

    assert result["status"] == "ok"
    assert result["true_provider_accessed"] == "official_directory"
    assert result["true_request_count"] == 1
    assert result["search_results_seen"] >= 4
    assert result["candidates_found"] >= 3
    assert result["pending_review_count"] >= 3
    assert result["connector_specs_generated"] >= 1
    assert {row[0] for row in statuses} == {"pending_review"}


def test_official_directory_execute_rejects_unallowlisted_directory_url(tmp_path) -> None:
    result = run_controlled_live_source_discovery(
        provider="official_directory",
        country="United States",
        disease="influenza",
        execute_live=True,
        allow_network=True,
        terms_reviewed=True,
        manual_reviewed=True,
        directory_url="https://example.com/not-allowed",
        max_requests=1,
        output_dir=str(tmp_path / "live_runs"),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="")),
    )

    assert result["status"] == "failed"
    assert result["network_accessed"] is False
    assert "allowlisted" in result["error"]
