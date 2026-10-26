from __future__ import annotations

import json

import httpx

from scripts.run_controlled_live_source_discovery import run_controlled_live_source_discovery
from source_discovery_agent.live_providers import LiveDiscoveryRequest, get_live_discovery_provider


def test_gdelt_provider_mock_429_is_structured_rate_limit() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(429, headers={"Retry-After": "120"}, json={"status": "limited"})

    result = get_live_discovery_provider("gdelt").discover(
        LiveDiscoveryRequest(
            country="United States",
            disease="influenza",
            provider="gdelt",
            allow_network=True,
            terms_reviewed=True,
            manual_reviewed=True,
            metadata={"execute_live": True, "transport": httpx.MockTransport(handler)},
        )
    )

    assert len(calls) == 1
    assert result.request_count == 1
    assert result.failure_type == "rate_limited"
    assert result.retry_allowed is False
    assert result.cooldown_recommended is True
    assert result.retry_after_seconds == 120
    rendered = json.dumps(result.metadata, ensure_ascii=False).lower()
    assert "response body" not in rendered


def test_controlled_runner_mock_429_does_not_retry_or_visit_candidates(tmp_path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(429, json={"status": "limited"})

    result = run_controlled_live_source_discovery(
        provider="gdelt",
        country="United States",
        disease="influenza",
        execute_live=True,
        allow_network=True,
        terms_reviewed=True,
        manual_reviewed=True,
        max_requests=1,
        output_dir=str(tmp_path / "runs"),
        transport=httpx.MockTransport(handler),
    )

    assert len(calls) == 1
    assert result["status"] == "failed"
    assert result["failure_type"] == "rate_limited"
    assert result["retry_allowed"] is False
    assert result["cooldown_recommended"] is True
    assert result["candidate_urls_accessed"] is False
    assert result["follow_links"] is False
