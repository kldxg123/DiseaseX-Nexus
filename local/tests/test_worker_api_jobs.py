from __future__ import annotations

import json

from fastapi.testclient import TestClient

from apps.api.deps import get_database_url
from apps.api.main import app


SENSITIVE_MARKERS = ["服务器信息.txt", "secret", "token", "password", "private key"]


def _database_url(tmp_path) -> str:
    return f"sqlite:///{(tmp_path / 'worker-api.db').as_posix()}"


def test_worker_jobs_api_lists_jobs() -> None:
    with TestClient(app) as client:
        response = client.get("/jobs")

    assert response.status_code == 200
    payload = response.json()
    assert any(job["name"] == "seed_sources" for job in payload["jobs"])


def test_worker_jobs_api_describes_schedule() -> None:
    with TestClient(app) as client:
        response = client.get("/jobs/schedule")

    assert response.status_code == 200
    payload = response.json()
    assert any(entry["name"] == "daily_clean" for entry in payload["schedule"])
    assert payload["warnings"] == []


def test_worker_jobs_api_runs_seed_sources_with_sqlite_override(tmp_path) -> None:
    database_url = _database_url(tmp_path)

    app.dependency_overrides[get_database_url] = lambda: database_url
    try:
        with TestClient(app) as client:
            response = client.post("/jobs/run", json={"job_name": "seed_sources", "params": {}})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["result"]["status"] == "success"
    assert payload["result"]["summary"]["sources_count"] > 0

    rendered = json.dumps(payload, ensure_ascii=False).lower()
    for marker in SENSITIVE_MARKERS:
        assert marker.lower() not in rendered
