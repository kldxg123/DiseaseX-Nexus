from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from apps.api.main import app
from common.config import get_settings


def test_health_endpoint_returns_ok_without_sensitive_fields() -> None:
    get_settings.cache_clear()
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "status": "ok",
        "service": "api",
        "environment": "development",
    }
    lowered_keys = {key.lower() for key in payload}
    assert not lowered_keys & {"secret", "token", "password", "passwd", "key", "credential"}
