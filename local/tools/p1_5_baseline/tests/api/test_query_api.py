from __future__ import annotations

import concurrent.futures
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from epidemic_core.api.app import create_app

TOKEN = "test-token-not-a-production-secret-0123456789"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("EPIDEMIC_QUERY_API_TOKEN", TOKEN)
    with TestClient(create_app()) as value:
        yield value


def headers(token=TOKEN):
    return {"Authorization": "Bearer " + token}


def payload(country="CN", disease="dengue", start="2025-01-01", end="2025-01-02"):
    return {"country": country, "disease": disease, "start_date": start, "end_date": end}


def test_auth_and_security_headers(client):
    assert client.get("/healthz", headers=headers()).status_code == 200
    for value in ({}, headers("wrong"), {"Authorization": "Basic malformed"}):
        assert client.get("/healthz", headers=value).status_code == 401
    response = client.get("/healthz", headers=headers())
    assert (
        response.headers["cache-control"] == "no-store"
        and response.headers["x-content-type-options"] == "nosniff"
        and response.headers.get("x-request-id")
    )
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path, headers=headers()).status_code == 404
    assert client.get("/healthz", headers={**headers(), "host": "evil.invalid"}).status_code == 400
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize(
    ("country", "disease", "alpha2", "slug"),
    [
        ("CN", "dengue", "CN", "dengue"),
        ("China", "登革热", "CN", "dengue"),
        ("中国", "Dengue Fever", "CN", "dengue"),
        ("US", "influenza", "US", "influenza"),
        ("USA", "流感", "US", "influenza"),
        ("美国", "flu", "US", "influenza"),
        ("BR", "COVID-19", "BR", "covid-19"),
        ("Brazil", "新冠肺炎", "BR", "covid-19"),
        ("巴西", "mpox", "BR", "mpox"),
        ("BR", "猴痘", "BR", "mpox"),
    ],
)
def test_normalize_aliases_zero_write(client, country, disease, alpha2, slug):
    engine = create_engine(os.environ["APP_DATABASE_URL"])
    before = engine.connect().execute(text("SELECT count(*) FROM epidemic.query_requests")).scalar_one()
    engine.dispose()
    r = client.post("/v1/queries/normalize", headers=headers(), json=payload(country, disease))
    assert (
        r.status_code == 200
        and r.json()["country_alpha2"] == alpha2
        and r.json()["disease_slug"] == slug
        and r.json()["persisted"] is False
        and r.json()["retrieval_started"] is False
    )
    engine = create_engine(os.environ["APP_DATABASE_URL"])
    after = engine.connect().execute(text("SELECT count(*) FROM epidemic.query_requests")).scalar_one()
    engine.dispose()
    assert before == after


@pytest.mark.parametrize(
    "body",
    [
        payload("Atlantis"),
        payload(disease="unknown"),
        payload(start="2025/01/01"),
        payload(start="2025-02-02", end="2025-01-01"),
        {**payload(), "extra": "x"},
        payload(country=""),
        payload(country="x" * 129),
    ],
)
def test_invalid_inputs_are_sanitized(client, body):
    r = client.post("/v1/queries/normalize", headers=headers(), json=body)
    assert r.status_code in (404, 409, 422) and "traceback" not in r.text.lower() and "postgresql" not in r.text.lower()


def test_register_idempotent_aliases_concurrent_and_read(client):
    variants = [payload("CN", "dengue"), payload("China", "Dengue Fever"), payload("中国", "登革热")]
    responses = [client.post("/v1/queries", headers=headers(), json=x) for x in variants]
    assert responses[0].status_code == 201 and all(x.status_code == 200 for x in responses[1:])
    data = [x.json() for x in responses]
    assert (
        len({x["query_id"] for x in data}) == 1
        and len({x["fingerprint"] for x in data}) == 1
        and all(x["retrieval_started"] is False for x in data)
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        more = list(pool.map(lambda _: client.post("/v1/queries", headers=headers(), json=variants[_ % 3]), range(10)))
    assert all(x.status_code == 200 for x in more)
    query_id = data[0]["query_id"]
    got = client.get("/v1/queries/" + query_id, headers=headers())
    assert got.status_code == 200 and got.json()["query_id"] == query_id and "raw" not in got.text.lower()
    assert client.get("/v1/queries/" + str(uuid.uuid4()), headers=headers()).status_code == 404
    assert client.get("/v1/queries/not-a-uuid", headers=headers()).status_code == 422
    changed = client.post("/v1/queries", headers=headers(), json=payload(end="2025-01-03"))
    assert changed.status_code == 201 and changed.json()["fingerprint"] != data[0]["fingerprint"]
    concurrent_payload = payload(start="2025-03-01", end="2025-03-02")
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        first_wave = list(
            pool.map(lambda _: client.post("/v1/queries", headers=headers(), json=concurrent_payload), range(10))
        )
    assert sorted(x.status_code for x in first_wave) == [200] * 9 + [201]
    assert len({x.json()["query_id"] for x in first_wave}) == 1
    engine = create_engine(os.environ["APP_DATABASE_URL"])
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM epidemic.query_requests")).scalar_one() == 3
        for table in ("retrieval_runs", "source_fetches", "raw_artifacts", "normalized_observations"):
            assert conn.execute(text("SELECT count(*) FROM epidemic." + table)).scalar_one() == 0
    engine.dispose()


def test_payload_limit_and_parallel_short_requests(client):
    r = client.post("/v1/queries/normalize", headers=headers(), content=b"{" + b"x" * 9000 + b"}")
    assert r.status_code == 413
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        responses = list(pool.map(lambda _: client.get("/healthz", headers=headers()), range(20)))
    assert all(x.status_code == 200 for x in responses)


def test_database_failure_is_sanitized(monkeypatch):
    monkeypatch.setenv("EPIDEMIC_QUERY_API_TOKEN", TOKEN)
    monkeypatch.setenv(
        "APP_DATABASE_URL", "postgresql" + "+psycopg://REDACTED@127.0.0.1:6543/unavailable"
    )
    with TestClient(create_app()) as unavailable:
        response = unavailable.get("/readyz", headers=headers())
    assert response.status_code == 503
    assert response.json() == {"error": {"code": "DATABASE_UNAVAILABLE", "message": "Database unavailable"}}
    assert "postgresql" not in response.text.lower() and "placeholder" not in response.text
