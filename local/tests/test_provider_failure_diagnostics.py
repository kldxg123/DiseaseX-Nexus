from __future__ import annotations

from live_source_search_orchestrator.orchestrator import build_provider_failure_diagnostics
from live_source_search_orchestrator.types import ProviderSearchResult


def test_provider_failure_diagnostics_are_structured_and_safe():
    results = [
        ProviderSearchResult(
            provider="gdelt",
            status="failed",
            request_count=1,
            search_results=[],
            network_accessed=True,
            failure_type="rate_limited",
            http_status_code=429,
            retry_allowed=False,
            safe_failure=True,
            sanitized_error_summary="GDELT request returned HTTP 429.",
            candidate_urls_visited=False,
            follow_links=False,
        )
    ]

    diagnostics = build_provider_failure_diagnostics(results)

    assert diagnostics == [
        {
            "provider": "gdelt",
            "status": "failed",
            "failure_type": "rate_limited",
            "http_status_code": 429,
            "request_count": 1,
            "network_accessed": True,
            "retry_allowed": False,
            "safe_failure": True,
            "sanitized_error_summary": "GDELT request returned HTTP 429.",
            "candidate_urls_visited": False,
            "follow_links": False,
        }
    ]
