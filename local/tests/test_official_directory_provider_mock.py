from __future__ import annotations

import json
from pathlib import Path

import httpx

from source_discovery_agent.live_providers import LiveDiscoveryRequest, get_live_discovery_provider

FIXTURE = Path("tests/fixtures/official_directory_fluview_mock.html")


def test_official_directory_provider_mock_live_parses_directory_links() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text=FIXTURE.read_text(encoding="utf-8"))

    result = get_live_discovery_provider("official_directory").discover(
        LiveDiscoveryRequest(
            country="United States",
            disease="influenza",
            provider="official_directory",
            allow_network=True,
            terms_reviewed=True,
            manual_reviewed=True,
            metadata={
                "execute_live": True,
                "directory_url": "https://www.cdc.gov/flu/weekly/index.htm",
                "transport": httpx.MockTransport(handler),
            },
        )
    )

    assert len(calls) == 1
    assert result.request_count == 1
    assert result.network_accessed is True
    assert len(result.search_results) >= 4
    assert all(row["url"].startswith("https://") for row in result.search_results)
    assert not any("not a url" in row["url"] for row in result.search_results)
    rendered = json.dumps(result.search_results, ensure_ascii=False).lower()
    assert "<html" not in rendered
    assert "password" not in rendered
    assert "token" not in rendered


def test_official_directory_provider_refuses_live_without_directory_url() -> None:
    result = get_live_discovery_provider("official_directory").discover(
        LiveDiscoveryRequest(
            country="United States",
            disease="influenza",
            provider="official_directory",
            allow_network=True,
            metadata={"execute_live": True},
        )
    )

    assert result.request_count == 0
    assert result.network_accessed is False
    assert result.failure_type == "invalid_provider_config"
