from __future__ import annotations

import httpx
import pytest

from apps.dashboard.api_client import DashboardApiClient, DashboardApiError


def test_dashboard_api_client_uses_default_and_env_base_url(monkeypatch) -> None:
    monkeypatch.delenv("API_BASE_URL", raising=False)
    assert DashboardApiClient().base_url == "http://localhost:8000"

    monkeypatch.setenv("API_BASE_URL", "http://localhost:9000/")
    assert DashboardApiClient().base_url == "http://localhost:9000"


def _transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "service": "api"})
        if request.url.path == "/sources":
            return httpx.Response(
                200,
                json={"sources": [{"id": 1, "name": "Fixture", "metadata_json": {"token": "hidden"}}]},
            )
        if request.url.path == "/sources/health":
            return httpx.Response(200, json={"total": 1, "by_status": {"active": 1}})
        if request.url.path == "/crawl/mock-url":
            return httpx.Response(200, json={"status": "ok", "summary": {"saved": True, "html": "<html>secret</html>"}})
        if request.url.path == "/crawl/discover-gdelt-mock":
            return httpx.Response(200, json={"status": "ok", "summary": {"upserted": 1}})
        if request.url.path == "/crawl/parse-official-fixture":
            return httpx.Response(200, json={"status": "ok", "summary": {"parsed": 1}})
        if request.url.path == "/predictions":
            return httpx.Response(200, json={"predictions": [{"id": 1, "predicted_value": 3.0}]})
        if request.url.path == "/metrics/backtest":
            return httpx.Response(200, json={"model_name": "feature_rule", "backtest_records": 1})
        if request.url.path == "/feedback":
            return httpx.Response(200, json={"feedback": [{"id": 1, "notes": "secret-token-value"}]})
        if request.url.path == "/feedback/summary":
            return httpx.Response(200, json={"feedback_count": 0})
        if request.url.path == "/experience/build":
            return httpx.Response(200, json={"status": "ok", "summary": {"experience_id": 1}})
        if request.url.path == "/experience/summary":
            return httpx.Response(200, json={"experience_count": 1})
        if request.url.path == "/analysis/minimum-data":
            return httpx.Response(200, json={"minimum_feature_set": ["case_history"], "feature_json": {"hidden": True}})
        if request.url.path == "/analysis/model-promotion-check":
            return httpx.Response(200, json={"promote": False, "lesson_json": {"hidden": True}})
        return httpx.Response(500, json={"detail": "secret-token-value"})

    return httpx.MockTransport(handler)


def test_dashboard_api_client_uses_mock_transport_without_real_requests() -> None:
    client = DashboardApiClient("http://localhost:8000", transport=_transport())

    assert client.health()["status"] == "ok"
    assert client.get_sources()[0]["name"] == "Fixture"
    assert "metadata_json" not in client.get_sources()[0]
    assert client.get_predictions()[0]["predicted_value"] == 3.0
    assert client.get_feedback_summary()["feedback_count"] == 0


def test_dashboard_api_client_covers_business_api_aliases_without_real_requests() -> None:
    client = DashboardApiClient("http://localhost:8000", transport=_transport())

    assert client.get_source_health()["total"] == 1
    assert client.crawl_mock_url({"source_id": "1", "url": "https://fixture.invalid", "html": "<html></html>"})[
        "summary"
    ]["saved"] is True
    assert client.discover_gdelt_mock({"country": "A", "disease": "B", "mock_response_json": {"articles": []}})[
        "summary"
    ]["upserted"] == 1
    assert client.parse_official_fixture({"connector": "who_disease_outbreak_news", "fixture_content": "{}"})[
        "summary"
    ]["parsed"] == 1
    assert client.get_backtest_metrics(model_name="feature_rule")["model_name"] == "feature_rule"
    assert client.get_feedback()[0]["notes"] == "***"
    assert client.build_experience({"feedback_id": "1"})["summary"]["experience_id"] == 1
    assert client.get_experience_summary()["experience_count"] == 1
    assert "feature_json" not in str(client.minimum_data_analysis({}))
    assert "lesson_json" not in str(client.model_promotion_check({}))


def test_dashboard_api_client_error_is_sanitized() -> None:
    client = DashboardApiClient("http://localhost:8000", transport=_transport())

    with pytest.raises(DashboardApiError) as exc_info:
        client.get_events()

    message = str(exc_info.value)
    assert "status=500" in message
    assert "secret-token-value" not in message
    assert "***" in message
