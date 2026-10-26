from __future__ import annotations

import json

import httpx

from scripts.run_controlled_live_source_discovery import run_controlled_live_source_discovery


def test_controlled_live_source_discovery_execute_mock_output_is_safe(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "articles": [
                    {
                        "title": "United States influenza surveillance data with secret-looking source text",
                        "url": "https://safe.example.gov/flu/data",
                        "domain": "safe.example.gov",
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
        transport=httpx.MockTransport(handler),
    )
    rendered = json.dumps(result, ensure_ascii=False).lower()

    assert result["status"] == "ok"
    for forbidden in (
        "服务器信息.txt",
        "password",
        "token",
        "secret",
        "ssh",
        "snippet",
        "raw_text",
        "metadata_json",
        "provenance_json",
        "feature_json",
        "lesson_json",
        "<html",
        "safe.example.gov/flu/data",
    ):
        assert forbidden not in rendered
