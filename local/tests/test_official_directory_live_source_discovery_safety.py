from __future__ import annotations

import json
from pathlib import Path

import httpx

from scripts.run_controlled_live_source_discovery import run_controlled_live_source_discovery

FIXTURE = Path("tests/fixtures/official_directory_fluview_mock.html")


def test_official_directory_execute_mock_output_is_safe(tmp_path) -> None:
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
        output_dir=str(tmp_path / "live_runs"),
        transport=httpx.MockTransport(handler),
    )
    rendered = json.dumps(result, ensure_ascii=False).lower()

    assert result["candidate_urls_accessed"] is False
    assert result["follow_links"] is False
    for forbidden in (
        "服务器信息.txt",
        "password",
        "token",
        "secret",
        "ssh",
        "raw_text",
        "metadata_json",
        "provenance_json",
        "feature_json",
        "lesson_json",
        "<html",
        "flu/weekly/index.htm",
        "fluviewinteractive.htm",
    ):
        assert forbidden not in rendered
