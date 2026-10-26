from __future__ import annotations

import sqlite3

import httpx

from scripts.run_controlled_live_source_discovery import run_controlled_live_source_discovery


def test_gdelt_live_source_discovery_registry_rows_pending_review(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "articles": [
                    {
                        "title": "United States influenza surveillance data",
                        "url": "https://statehealth.example.gov/flu/data",
                        "domain": "statehealth.example.gov",
                        "sourcecountry": "US",
                        "language": "English",
                    }
                ]
            },
        )

    result = run_controlled_live_source_discovery(
        provider="gdelt",
        country="United States",
        disease="influenza",
        execute_live=True,
        allow_network=True,
        terms_reviewed=True,
        manual_reviewed=True,
        output_dir=str(tmp_path / "live_runs"),
        max_requests=1,
        transport=httpx.MockTransport(handler),
    )

    with sqlite3.connect(result["live_discovery_db_path"]) as connection:
        rows = connection.execute("SELECT status FROM source_registry").fetchall()

    assert result["status"] == "ok"
    assert rows
    assert {row[0] for row in rows} == {"pending_review"}
    assert result["connector_specs_generated"] >= 1
