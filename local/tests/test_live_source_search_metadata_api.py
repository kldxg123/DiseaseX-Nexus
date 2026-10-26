from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.deps import get_db_session
from apps.api.main import app
from common.db import get_session, init_db


def test_live_source_search_metadata_api_requires_flags(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'api.sqlite'}"
    init_db(database_url).dispose()

    def override_session():
        with get_session(database_url) as session:
            yield session

    app.dependency_overrides[get_db_session] = override_session
    client = TestClient(app)
    try:
        response = client.post(
            "/live-source-search/metadata-check",
            json={"country": "United States", "disease": "measles", "provider": "auto"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["metadata_check_request_count"] == 0
    assert payload["full_body_saved"] is False
