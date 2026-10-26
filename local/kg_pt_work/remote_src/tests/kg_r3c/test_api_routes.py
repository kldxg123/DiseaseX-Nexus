# ruff: noqa: E501
"""Route tests for epidemic_core.kg_r3c.dashboard_app with fake engine/connection.

No live DB required: the read path uses a fake SQLAlchemy engine and the write
path monkeypatches ``write_conn`` with a fake psycopg connection.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from epidemic_core.kg_r3c import dashboard_app as kg_dash

AUTH_COOKIE = "epidemic_dashboard_session"

GET_ROUTES = [
    "/api/v1/kg/import-runs",
    f"/api/v1/kg/import-runs/{uuid.uuid4()}",
    "/api/v1/kg/review-items",
    "/api/v1/kg/graph-versions",
    f"/api/v1/kg/graph-versions/{uuid.uuid4()}",
    "/api/v1/kg/subgraphs?subject_kind=WORK&subject_id=w1",
    f"/api/v1/kg/papers/{uuid.uuid4()}/evidence-spans",
    f"/api/v1/kg/data-profiles/{uuid.uuid4()}",
    "/api/v1/kg/r3c/catalog",
    "/api/v1/kg/r3c/recommendations",
    f"/api/v1/kg/r3c/recommendations/{uuid.uuid4()}",
    "/api/v1/kg/openapi",
]

POST_ROUTES = [
    "/api/v1/kg/import-runs",
    f"/api/v1/kg/import-runs/{uuid.uuid4()}/extract",
    f"/api/v1/kg/review-items/{uuid.uuid4()}/decisions",
    "/api/v1/kg/data-profile-runs",
    "/api/v1/kg/recommendation-runs",
    f"/api/v1/kg/r3c/recommendations/{uuid.uuid4()}/execution-contracts",
]


def _norm(sql: Any) -> str:
    return " ".join(str(sql).split())


class _FakeMappings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _FakeMappings:
        return _FakeMappings(self._rows)


class _FakeConnection:
    def __init__(self, handler: Any) -> None:
        self.handler = handler

    def execute(self, statement: Any, params: Any = None) -> _FakeResult:
        return _FakeResult(self.handler(_norm(statement), dict(params or {})))


class _FakeEngine:
    def __init__(self, handler: Any) -> None:
        self.handler = handler

    def connect(self) -> Any:
        conn = _FakeConnection(self.handler)

        class _Ctx:
            def __enter__(self) -> _FakeConnection:
                return conn

            def __exit__(self, *args: Any) -> bool:
                return False

        return _Ctx()


class _FakeCursor:
    def __init__(self, conn: "_FakeWriteConn") -> None:
        self.conn = conn
        self._one: dict[str, Any] | None = None
        self._all: list[dict[str, Any]] = []

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *args: Any) -> bool:
        return False

    def execute(self, sql: str, params: Any = None) -> None:
        self.conn.calls.append((_norm(sql), tuple(params or ())))
        self._one, self._all = self.conn.handler(_norm(sql), tuple(params or ()))

    def fetchone(self) -> dict[str, Any] | None:
        return self._one

    def fetchall(self) -> list[dict[str, Any]]:
        return self._all


class _FakeWriteConn:
    def __init__(self, handler: Any) -> None:
        self.handler = handler
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def __enter__(self) -> "_FakeWriteConn":
        return self

    def __exit__(self, *args: Any) -> bool:
        return False

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)


def _json_response(value: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(value, status_code=status_code)


def _build_app(engine: Any) -> FastAPI:
    app = FastAPI()
    kg_dash.install_routes(
        app,
        engine,
        lambda request: bool(request.cookies.get(AUTH_COOKIE)),
        lambda: JSONResponse({"detail": "AUTHENTICATION_REQUIRED"}, status_code=401),
        _json_response,
    )
    return app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(_build_app(_FakeEngine(lambda sql, params: [])))


def _authed(c: TestClient) -> TestClient:
    c.cookies.set(AUTH_COOKIE, "fake-session")
    return c


def test_unauthenticated_gets_401_on_all_read_routes(client: TestClient) -> None:
    for path in GET_ROUTES:
        response = client.get(path)
        assert response.status_code == 401, path
        assert response.json()["detail"] == "AUTHENTICATION_REQUIRED"


def test_unauthenticated_gets_401_on_all_write_routes(client: TestClient) -> None:
    for path in POST_ROUTES:
        response = client.post(path, json={})
        assert response.status_code == 401, path
        assert response.json()["detail"] == "AUTHENTICATION_REQUIRED"


def test_authenticated_read_import_run() -> None:
    run_id = str(uuid.uuid4())

    def handler(sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if "kg_import_members" in sql:
            return [{"kind": "PDF", "member_count": 3, "total_bytes": 1024, "quarantined_count": 1}]
        if "from epidemic_kg.kg_import_runs" in sql:
            return [{
                "import_run_id": run_id,
                "import_key": "archive-2026-08",
                "archive_path": "/data/archive.zip",
                "archive_sha256": "ab" * 32,
                "status": "OPEN",
                "actor": "dashboard_user",
                "extractor_versions": {},
                "counts": {"members": 3},
                "started_at": None,
                "finished_at": None,
            }]
        return []

    client = _authed(TestClient(_build_app(_FakeEngine(handler))))
    response = client.get(f"/api/v1/kg/import-runs/{run_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["import_run"]["import_key"] == "archive-2026-08"
    assert body["import_run"]["status"] == "OPEN"
    assert body["member_accounting"][0]["member_count"] == 3


def test_authenticated_read_import_run_not_found(client: TestClient) -> None:
    _authed(client)
    response = client.get(f"/api/v1/kg/import-runs/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["detail"] == "IMPORT_RUN_NOT_FOUND"


def test_authenticated_read_recommendations() -> None:
    rec_id = str(uuid.uuid4())
    seen_params: list[dict[str, Any]] = []

    def handler(sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if "from epidemic_kg.kg_recommendations r" in sql:
            seen_params.append(params)
            return [{
                "recommendation_id": rec_id,
                "run_id": str(uuid.uuid4()),
                "profile_id": str(uuid.uuid4()),
                "adapter_id": "naive_seasonal",
                "bundle_id": str(uuid.uuid4()),
                "is_baseline": False,
                "total_score": 82.5,
                "parameter_confidence_score": 77.0,
                "rank": 1,
                "eligibility_status": "ELIGIBLE",
                "ineligibility_reasons": [],
                "gap_list": [],
                "country": "FIN",
                "disease": "hantavirus",
                "indicator": "puumala_virus_reports",
                "frequency": "week",
                "model_family": "NAIVE",
                "adapter_version": "1.0.0",
                "bundle_sha256": "cd" * 32,
                "bundle_completeness": 0.9,
            }]
        return []

    client = _authed(TestClient(_build_app(_FakeEngine(handler))))
    response = client.get("/api/v1/kg/r3c/recommendations?country=FIN&frequency=week&eligible_only=true")
    assert response.status_code == 200
    body = response.json()
    assert body["score_type"] == "PRE_EXECUTION_EVIDENCE_SCORE"
    assert body["execution_status"] == "DEFERRED_NOT_EXECUTED"
    assert body["items"][0]["recommendation_id"] == rec_id
    assert body["items"][0]["country"] == "FIN"
    assert seen_params[0]["frequency"] == "week"


def test_authenticated_read_r3c_catalog() -> None:
    def handler(sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if "select distinct s.country" in sql:
            return [{
                "country": "BRA",
                "disease": "dengue",
                "indicator": "cases_new",
                "frequency": "week",
            }]
        return []

    client = _authed(TestClient(_build_app(_FakeEngine(handler))))
    response = client.get("/api/v1/kg/r3c/catalog")
    assert response.status_code == 200
    assert response.json() == {
        "score_type": "PRE_EXECUTION_EVIDENCE_SCORE",
        "execution_status": "DEFERRED_NOT_EXECUTED",
        "items": [{
            "country": "BRA",
            "disease": "dengue",
            "indicator": "cases_new",
            "frequency": "week",
        }],
    }


def test_post_import_runs_idempotent_by_import_key(monkeypatch: Any) -> None:
    store: dict[str, dict[str, Any]] = {}

    def handler(sql: str, params: tuple[Any, ...]) -> tuple[Any, list[Any]]:
        if sql.startswith("SELECT audit_id"):
            return None, []
        if "INSERT INTO epidemic_kg.kg_import_runs" in sql:
            key = params[1]
            if key in store:
                return None, []
            store[key] = {"import_run_id": params[0], "status": "OPEN"}
            return dict(store[key]), []
        if "SELECT import_run_id, status FROM epidemic_kg.kg_import_runs" in sql:
            return store.get(params[0]), []
        if "kg_api_audit_log" in sql:
            return None, []
        return None, []

    fake = _FakeWriteConn(handler)
    monkeypatch.setattr(kg_dash, "write_conn", lambda: fake)
    client = _authed(TestClient(_build_app(_FakeEngine(lambda sql, params: []))))
    payload = {"import_key": "archive-1", "archive_path": "/a.zip", "archive_sha256": "ef" * 32}

    first = client.post("/api/v1/kg/import-runs", json=payload)
    assert first.status_code == 200
    second = client.post("/api/v1/kg/import-runs", json=payload)
    assert second.status_code == 200
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert first.json()["import_run_id"] == second.json()["import_run_id"]
    assert len(store) == 1


def test_post_review_decision_writes_decision_and_audit_log(monkeypatch: Any) -> None:
    item_id = str(uuid.uuid4())

    def handler(sql: str, params: tuple[Any, ...]) -> tuple[Any, list[Any]]:
        if sql.startswith("SELECT audit_id"):
            return None, []
        if "SELECT review_item_id, status FROM epidemic_kg.kg_review_items" in sql:
            return {"review_item_id": item_id, "status": "OPEN"}, []
        return None, []

    fake = _FakeWriteConn(handler)
    monkeypatch.setattr(kg_dash, "write_conn", lambda: fake)
    client = _authed(TestClient(_build_app(_FakeEngine(lambda sql, params: []))))
    response = client.post(
        f"/api/v1/kg/review-items/{item_id}/decisions",
        json={"decision": "APPROVE", "actor": "analyst_zhang", "rationale": "证据充分"},
        headers={"Idempotency-Key": "dec-1"},
    )
    assert response.status_code == 200
    assert response.json()["item_status"] == "APPROVE"
    sqls = [sql for sql, _ in fake.calls]
    assert any("INSERT INTO epidemic_kg.kg_review_decisions" in s for s in sqls)
    assert any("INSERT INTO epidemic_kg.kg_api_audit_log" in s for s in sqls)
    audit = next(p for s, p in fake.calls if "INSERT INTO epidemic_kg.kg_api_audit_log" in s)
    assert audit[3] == "dashboard_user"
    assert audit[4] == "dec-1"
    assert re.fullmatch(r"[0-9a-f]{64}", audit[5])


def test_post_review_decision_idempotent_replay(monkeypatch: Any) -> None:
    audit_rows: dict[tuple[str, str], str] = {}
    import_store: dict[str, dict[str, Any]] = {}

    def handler(sql: str, params: tuple[Any, ...]) -> tuple[Any, list[Any]]:
        if sql.startswith("SELECT audit_id"):
            key = (params[0], params[1])
            return ({"audit_id": audit_rows[key]} if key in audit_rows else None), []
        if "INSERT INTO epidemic_kg.kg_import_runs" in sql:
            key = params[1]
            if key in import_store:
                return None, []
            import_store[key] = {"import_run_id": params[0], "status": "OPEN"}
            return dict(import_store[key]), []
        if "SELECT import_run_id, status FROM epidemic_kg.kg_import_runs" in sql:
            return import_store.get(params[0]), []
        if "INSERT INTO epidemic_kg.kg_api_audit_log" in sql:
            audit_rows[(params[1], params[4])] = params[0]
            return None, []
        return None, []

    fake = _FakeWriteConn(handler)
    monkeypatch.setattr(kg_dash, "write_conn", lambda: fake)
    client = _authed(TestClient(_build_app(_FakeEngine(lambda sql, params: []))))
    payload = {"import_key": "k-replay", "archive_path": "/a.zip", "archive_sha256": "ef" * 32}
    first = client.post("/api/v1/kg/import-runs", json=payload, headers={"Idempotency-Key": "rk-1"})
    assert first.status_code == 200
    replay = client.post("/api/v1/kg/import-runs", json=payload, headers={"Idempotency-Key": "rk-1"})
    assert replay.status_code == 200
    assert replay.headers.get("Idempotent-Replay") == "true"
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["audit_id"] == first.json()["audit_id"]


def test_post_execution_contract_deferred_with_hashes(monkeypatch: Any) -> None:
    rec_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    profile_id = str(uuid.uuid4())
    graph_version_id = str(uuid.uuid4())
    inserted: dict[str, Any] = {}

    def handler(sql: str, params: tuple[Any, ...]) -> tuple[Any, list[Any]]:
        if sql.startswith("SELECT audit_id"):
            return None, []
        if "kg_execution_contracts" in sql and "INSERT INTO" in sql:
            inserted["sql"] = sql
            inserted["params"] = params
            return {"contract_id": params[0], "status": "DEFERRED_NOT_EXECUTED"}, []
        if "kg_forecast_tasks" in sql:
            return {
                "task_sha256": "11" * 32,
                "horizons": ["H1", "H2", "H3", "H4"],
                "target_semantics": "weekly incident reports",
                "leakage_boundary": "train_periods < cutoff <= test_periods",
            }, []
        if "kg_score_policies" in sql:
            return {"policy_sha256": "22" * 32, "policy_version": "v1"}, []
        if "kg_graph_versions" in sql:
            return {"document_set_hash": "33" * 32, "assertion_set_hash": "44" * 32}, []
        if "from epidemic_kg.kg_recommendations r" in sql:
            return {
                "recommendation_id": rec_id,
                "run_id": run_id,
                "profile_id": profile_id,
                "adapter_id": "naive_seasonal",
                "bundle_id": str(uuid.uuid4()),
                "country": "FIN",
                "disease": "hantavirus",
                "indicator": "puumala_virus_reports",
                "frequency": "week",
                "snapshot_sha256": "55" * 32,
                "profile_sha256": "66" * 32,
                "profiler_version": "r3c.profiler.v1",
                "bundle_sha256": "77" * 32,
                "bundle_version": "v1",
                "adapter_version": "1.0.0",
                "model_family": "NAIVE",
                "graph_version_id": graph_version_id,
                "policy_id": "r3c.policy.v1",
                "profile_set_hash": "88" * 32,
            }, []
        return None, []

    fake = _FakeWriteConn(handler)
    monkeypatch.setattr(kg_dash, "write_conn", lambda: fake)
    client = _authed(TestClient(_build_app(_FakeEngine(lambda sql, params: []))))
    response = client.post(f"/api/v1/kg/r3c/recommendations/{rec_id}/execution-contracts", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "DEFERRED_NOT_EXECUTED"
    assert re.fullmatch(r"[0-9a-f]{64}", body["contract_sha256"])
    contract = body["contract"]
    assert contract["contract_version"] == "v1"
    assert contract["execution_status"] == "DEFERRED_NOT_EXECUTED"
    assert contract["horizons"] == ["H1", "H2", "H3", "H4"]
    assert contract["leakage_boundary"] == "train_periods < cutoff <= test_periods"
    bindings = contract["bindings"]
    for field in ("snapshot_sha256", "profile_sha256", "task_sha256", "document_set_hash", "assertion_set_hash", "policy_sha256", "bundle_sha256", "profile_set_hash"):
        assert bindings[field], field
    assert bindings["graph_version_id"] == graph_version_id
    assert contract["target"] == {"country": "FIN", "disease": "hantavirus", "indicator": "puumala_virus_reports", "frequency": "week"}
    assert contract["probabilistic_interval_requirement"]["required"] is True
    assert "INSERT INTO epidemic_kg.kg_execution_contracts" in inserted["sql"]
    assert any("INSERT INTO epidemic_kg.kg_api_audit_log" in s for s, _ in fake.calls)


def test_post_recommendation_runs_conflict_when_empty(monkeypatch: Any) -> None:
    def handler(sql: str, params: tuple[Any, ...]) -> tuple[Any, list[Any]]:
        return None, []

    fake = _FakeWriteConn(handler)
    monkeypatch.setattr(kg_dash, "write_conn", lambda: fake)
    client = _authed(TestClient(_build_app(_FakeEngine(lambda sql, params: []))))
    response = client.post("/api/v1/kg/recommendation-runs", json={})
    assert response.status_code == 409
    assert response.json()["detail"] == "RECOMPUTE_REQUIRES_OFFLINE_PIPELINE"


def test_openapi_contains_new_paths(client: TestClient) -> None:
    _authed(client)
    response = client.get("/api/v1/kg/openapi")
    assert response.status_code == 200
    paths = response.json()["paths"]
    expected = {
        "/api/v1/kg/import-runs",
        "/api/v1/kg/import-runs/{import_run_id}",
        "/api/v1/kg/import-runs/{import_run_id}/extract",
        "/api/v1/kg/review-items",
        "/api/v1/kg/review-items/{review_item_id}/decisions",
        "/api/v1/kg/graph-versions/{graph_version_id}",
        "/api/v1/kg/subgraphs",
        "/api/v1/kg/papers/{work_id}/evidence-spans",
        "/api/v1/kg/data-profiles/{profile_id}",
        "/api/v1/kg/data-profile-runs",
        "/api/v1/kg/r3c/recommendations",
        "/api/v1/kg/r3c/recommendations/{recommendation_id}",
        "/api/v1/kg/recommendation-runs",
        "/api/v1/kg/r3c/recommendations/{recommendation_id}/execution-contracts",
    }
    assert expected <= set(paths)
