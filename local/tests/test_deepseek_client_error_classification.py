from __future__ import annotations

import httpx

from deepseek_web_search_agent.config import DeepSeekWebSearchConfig
from deepseek_web_search_agent.deepseek_client import (
    DeepSeekClient,
    classify_http_status,
    parse_json_or_fallback,
)


def _config() -> DeepSeekWebSearchConfig:
    return DeepSeekWebSearchConfig(
        deepseek_api_key='REDACTED',
        deepseek_base_url="https://deepseek.test",
        deepseek_model="deepseek-chat",
        deepseek_timeout_seconds=5.0,
        general_web_search_provider="mock",
        provider_api_keys={},
        max_search_queries=1,
        max_search_results_per_query=1,
        max_pages_to_read=0,
        max_total_http_requests=1,
    )


def _client_for_status(status_code: int) -> DeepSeekClient:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": {"message": "safe test error"}})

    return DeepSeekClient(_config(), transport=httpx.MockTransport(handler))


def test_classify_http_status_maps_common_deepseek_errors() -> None:
    assert classify_http_status(400) == "invalid_payload"
    assert classify_http_status(400, error_summary="model unavailable") == "model_not_available"
    assert classify_http_status(401) == "unauthorized"
    assert classify_http_status(403) == "forbidden"
    assert classify_http_status(429) == "rate_limited"
    assert classify_http_status(429, error_summary="quota exceeded") == "quota_or_billing"
    assert classify_http_status(500) == "server_error"
    assert classify_http_status(418) == "unknown_http_error"


def test_deepseek_client_returns_safe_http_error_metadata() -> None:
    result = _client_for_status(401).call_chat_json(
        system_prompt="Return JSON.",
        user_payload={"task": "diagnostic"},
        allow_deepseek_api=True,
        fallback_json={"ok": False},
    )

    assert result.called is True
    assert result.http_status_code == 401
    assert result.failure_type == "unauthorized"
    assert result.parsed_json == {"ok": False}
    assert "deepseek_api_http_error" in result.warnings


def test_deepseek_client_plain_text_json_mode_parses_embedded_json() -> None:
    parsed = parse_json_or_fallback("Here is the result: {\"ok\": true, \"count\": 2}", {})
    fenced = parse_json_or_fallback("```json\n{\"ok\": true}\n```", {})

    assert parsed == {"ok": True, "count": 2}
    assert fenced == {"ok": True}

