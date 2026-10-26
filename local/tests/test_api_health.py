from __future__ import annotations


def test_api_health_returns_ok(api_client) -> None:
    response = api_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "api",
        "environment": "development",
    }


def test_api_validation_error_does_not_echo_sensitive_input(api_client) -> None:
    response = api_client.post(
        "/sources/discover/gdelt/mock",
        json={
            "country": "United States",
            "disease": "norovirus",
            "lookback_days": "not-a-number-with-secret-token",
            "mock_response_json": {"articles": []},
        },
    )

    assert response.status_code == 422
    rendered = str(response.json()).lower()
    assert "not-a-number-with-secret-token" not in rendered
    assert "secret-token" not in rendered
