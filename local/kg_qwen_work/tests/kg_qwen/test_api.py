# ruff: noqa: E501
"""Pure API tests for kg_qwen.api (no database).

A minimal FastAPI app is built in-process; ``install_autotuning_routes`` is
wired with a fake read engine (canned SQL answers), a monkeypatched
``write_conn`` (the psycopg write seam, backed by a shared in-memory audit
store so the advisory-lock + audit-replay contract can be exercised by 8
concurrent same-key POSTs) and monkeypatched
``epidemic_core.kg_qwen.pipeline`` helpers. authorized/unauthorized/
json_response fakes mirror dashboard/app.py semantics (cookie session -> 401
JSON). The pattern follows remote_src/tests_kg_pt/test_pt_api.py.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from epidemic_core.kg_qwen import api as kq_api
from epidemic_core.kg_qwen import pipeline as kq_pipeline

RUN_ID = "11111111-1111-1111-1111-111111111111"
SHADOW_RUN_ID = "33333333-3333-3333-3333-333333333333"
SNAP_SHA = "s" * 64
TRACK_A = kq_pipeline.TRACK_A_KEY
TRACK_B = kq_pipeline.TRACK_B_KEY

BASE_OVERVIEW: dict[str, Any] = {
    "track_key": TRACK_A,
    "frequency": "month",
    "eval_identity": "REUSED_HISTORICAL_BENCHMARK",
    "eval_flags": ["REUSED_HISTORICAL_BENCHMARK", "NOT_FRESH_HELDOUT"],
    "run": {"run_id": RUN_ID, "status": "COMPLETED", "target_status": "TARGET_MET"},
    "shadow_run_id": SHADOW_RUN_ID,
    "snapshot": {"snapshot_id": "snap-1", "n_obs": 240, "snapshot_sha256": SNAP_SHA},
    "dataset_profile": {"n_obs": 204},
    "recommendation": {"adapter_id": "negbin_dglm.v1", "total_score": 80.88},
    "plans": [{"plan_id": "plan-1", "round_index": 1, "plan_source": "QWEN"}],
    "rounds": [{"round_id": "round-1", "round_index": 1, "best_val_wape": 0.28, "target_met": True}],
    "evaluation": {"identity": "REUSED_HISTORICAL_BENCHMARK", "wape": 0.41, "eval_count": 1},
    "feedback_summary": {"matured_count": 36, "awaiting_count": 4},
    "learning_cycles": [{"learning_cycle_id": "cycle-1"}],
    "champion_decisions": [{"champion_adapter_id": "seasonal_naive", "promotion_applied": False}],
    "target": {"wape_max": 0.3, "agreement_pct_min": 70.0, "gap_to_70pct": -2.0},
    "limitations": kq_pipeline.LIMITATIONS,
}

ENGINE_ANSWERS: list[tuple[str, list[dict[str, Any]]]] = [
    (
        "from epidemic_kg.kg_qwen_agent_runs",
        [
            {
                "run_id": RUN_ID,
                "track_key": TRACK_A,
                "status": "COMPLETED",
                "target_status": "TARGET_MET",
                "snapshot_sha256": SNAP_SHA,
                "split_sha256": "p" * 64,
            }
        ],
    ),
]


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _FakeResult:
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _FakeConnection:
    """Answers text() queries by substring match; never touches a database."""

    def __init__(self, answers: list[tuple[str, list[dict[str, Any]]]]) -> None:
        self._answers = answers

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def execute(self, clause: Any, params: Any = None) -> _FakeResult:
        sql = " ".join(str(clause).split())
        for key, rows in self._answers:
            if key in sql:
                return _FakeResult(rows)
        return _FakeResult([])


class _FakeEngine:
    def __init__(self, answers: list[tuple[str, list[dict[str, Any]]]] | None = None) -> None:
        self._answers = answers or []

    def connect(self) -> _FakeConnection:
        return _FakeConnection(self._answers)


class _SharedAuditStore:
    """In-memory audit log + advisory lock emulation shared across fake conns.

    The advisory xact lock is held from the lock statement until commit/close,
    which serializes concurrent same-key POSTs exactly like the real contract:
    the first request inserts the audit row, later ones replay it."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.audit_rows: dict[tuple[str, str], str] = {}


class _FakeCursor:
    def __init__(self, conn: _FakeWriteConn) -> None:
        self._conn = conn
        self._pending: str = ""

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def execute(self, sql: str, params: Any = None) -> None:
        normalized = " ".join(str(sql).split())
        self._conn.statements.append(normalized)
        self._pending = normalized
        store = self._conn.store
        if normalized.startswith("SELECT pg_advisory_xact_lock"):
            store.lock.acquire()
            self._conn.holds_lock = True
        elif normalized.startswith("SELECT audit_id"):
            self._conn.last_audit_lookup = (str(params[0]), str(params[1]))
        elif normalized.startswith("INSERT INTO epidemic_kg.kg_api_audit_log"):
            endpoint, key = str(params[1]), str(params[4])
            store.audit_rows.setdefault((endpoint, key), str(params[0]))

    def fetchone(self) -> dict[str, Any] | None:
        if self._pending.startswith("SELECT audit_id"):
            endpoint, key = self._conn.last_audit_lookup
            audit_id = self._conn.store.audit_rows.get((endpoint, key))
            return {"audit_id": audit_id} if audit_id else None
        return None


class _FakeWriteConn:
    def __init__(self, store: _SharedAuditStore) -> None:
        self.store = store
        self.statements: list[str] = []
        self.last_audit_lookup: tuple[str, str] = ("", "")
        self.committed = False
        self.holds_lock = False
        self._cursor = _FakeCursor(self)

    def __enter__(self) -> _FakeWriteConn:
        return self

    def __exit__(self, *exc: Any) -> bool:
        self._release()
        return False

    def _release(self) -> None:
        # only the conn that acquired the (unowned) threading.Lock releases it
        if self.holds_lock:
            self.holds_lock = False
            self.store.lock.release()

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True
        self._release()


def _authorized(request: Request) -> bool:
    return request.cookies.get("session") == "test-session"


def _unauthorized() -> JSONResponse:
    return JSONResponse({"detail": "UNAUTHORIZED"}, status_code=401)


def _json_response(payload: Any, status: int) -> JSONResponse:
    return JSONResponse(payload, status_code=status)


AUTH = {"session": "test-session"}


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(kq_api._pipeline, "autotuning_overview", lambda conn, track_key: BASE_OVERVIEW)
    monkeypatch.setattr(
        kq_api._pipeline,
        "autotuning_plans",
        lambda conn, run_id: [{"plan_id": "plan-1", "round_index": 1, "plan_source": "QWEN"}],
    )
    monkeypatch.setattr(
        kq_api._pipeline,
        "autotuning_rounds",
        lambda conn, run_id: [{"round_id": "round-1", "round_index": 1, "best_val_wape": 0.28}],
    )
    monkeypatch.setattr(
        kq_api._pipeline,
        "autotuning_trials",
        lambda conn, run_id, round_index: [
            {"trial_id": "t0", "round_index": 1, "trial_index": 0, "adapter_id": "negbin_dglm.v1"}
        ],
    )
    monkeypatch.setattr(
        kq_api._pipeline,
        "autotuning_evaluation",
        lambda conn, run_id: {"run_id": run_id, "identity": "FRESH_HELDOUT_LOCKED", "eval_count": 1, "wape": 0.29},
    )
    monkeypatch.setattr(
        kq_api._pipeline,
        "autotuning_feedback",
        lambda conn, run_id: {
            "run_id": run_id,
            "shadow_run_id": SHADOW_RUN_ID,
            "summary": {"matured_count": 36, "awaiting_count": 4},
            "rows": [],
        },
    )
    app = FastAPI()
    kq_api.install_autotuning_routes(app, _FakeEngine(ENGINE_ANSWERS), _authorized, _unauthorized, _json_response)
    return TestClient(app)


# ------------------------------------------------------------------ auth
def test_unauthenticated_gets_401(client: TestClient) -> None:
    for path in (
        "/api/v1/kg/autotuning/overview",
        f"/api/v1/kg/autotuning/plans?run_id={RUN_ID}",
        f"/api/v1/kg/autotuning/rounds?run_id={RUN_ID}",
        f"/api/v1/kg/autotuning/trials?run_id={RUN_ID}",
        f"/api/v1/kg/autotuning/evaluation?run_id={RUN_ID}",
        f"/api/v1/kg/autotuning/feedback?run_id={RUN_ID}",
    ):
        r = client.get(path)
        assert r.status_code == 401, path
        assert r.json()["detail"] == "UNAUTHORIZED"


def test_unauthenticated_posts_401(client: TestClient) -> None:
    r = client.post("/api/v1/kg/autotuning/run", json={"track_key": TRACK_A})
    assert r.status_code == 401
    r = client.post("/api/v1/kg/autotuning/reconcile", json={"run_id": RUN_ID})
    assert r.status_code == 401


# ------------------------------------------------------------------ reads
def test_overview_200_expected_keys(client: TestClient) -> None:
    r = client.get(f"/api/v1/kg/autotuning/overview?track_key={TRACK_A}", cookies=AUTH)
    assert r.status_code == 200
    d = r.json()
    for key in (
        "track_key",
        "run",
        "snapshot",
        "dataset_profile",
        "recommendation",
        "plans",
        "rounds",
        "evaluation",
        "feedback_summary",
        "learning_cycles",
        "champion_decisions",
        "target",
        "limitations",
    ):
        assert key in d, key
    assert d["run"]["run_id"] == RUN_ID
    assert d["eval_identity"] == "REUSED_HISTORICAL_BENCHMARK"
    assert any("Shadow" in s for s in d["limitations"])
    assert any("WAPE" in s for s in d["limitations"])
    assert any("NOT_FRESH" in s for s in d["limitations"])


def test_overview_404_when_no_run(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kq_api._pipeline, "autotuning_overview", lambda conn, track_key: None)
    r = client.get(f"/api/v1/kg/autotuning/overview?track_key={TRACK_B}", cookies=AUTH)
    assert r.status_code == 404
    assert r.json()["detail"] == "KQ_AUTOTUNING_NOT_FOUND"


def test_overview_422_unknown_track(client: TestClient) -> None:
    r = client.get("/api/v1/kg/autotuning/overview?track_key=XXX:nope:nope:day", cookies=AUTH)
    assert r.status_code == 422
    assert r.json()["detail"] == "KQ_UNKNOWN_TRACK"


def test_plans_rounds_trials_200(client: TestClient) -> None:
    r = client.get(f"/api/v1/kg/autotuning/plans?run_id={RUN_ID}", cookies=AUTH)
    assert r.status_code == 200
    assert r.json()["plans"][0]["plan_source"] == "QWEN"
    r = client.get(f"/api/v1/kg/autotuning/rounds?run_id={RUN_ID}", cookies=AUTH)
    assert r.status_code == 200
    assert r.json()["rounds"][0]["round_index"] == 1
    r = client.get(f"/api/v1/kg/autotuning/trials?run_id={RUN_ID}&round_index=1", cookies=AUTH)
    assert r.status_code == 200
    d = r.json()
    assert d["trials"][0]["trial_index"] == 0
    assert d["round_index"] == 1
    assert d["truncated"] is False


def test_plans_rounds_trials_404_and_422(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kq_api._pipeline, "autotuning_plans", lambda conn, run_id: [])
    monkeypatch.setattr(kq_api._pipeline, "autotuning_rounds", lambda conn, run_id: [])
    monkeypatch.setattr(kq_api._pipeline, "autotuning_trials", lambda conn, run_id, round_index: [])
    for path in ("plans", "rounds", "trials"):
        r = client.get(f"/api/v1/kg/autotuning/{path}?run_id={RUN_ID}", cookies=AUTH)
        assert r.status_code == 404, path
        assert r.json()["detail"] == "KQ_RUN_NOT_FOUND"
        r = client.get(f"/api/v1/kg/autotuning/{path}?run_id=not-a-uuid", cookies=AUTH)
        assert r.status_code == 422, path
        assert r.json()["detail"] == "INVALID_ID"


def test_evaluation_200_404_422(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    r = client.get(f"/api/v1/kg/autotuning/evaluation?run_id={RUN_ID}", cookies=AUTH)
    assert r.status_code == 200
    d = r.json()
    assert d["identity"] == "FRESH_HELDOUT_LOCKED"
    assert d["eval_count"] == 1
    monkeypatch.setattr(kq_api._pipeline, "autotuning_evaluation", lambda conn, run_id: None)
    r = client.get(f"/api/v1/kg/autotuning/evaluation?run_id={RUN_ID}", cookies=AUTH)
    assert r.status_code == 404
    r = client.get("/api/v1/kg/autotuning/evaluation?run_id=bad", cookies=AUTH)
    assert r.status_code == 422


def test_feedback_200_404_422(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    r = client.get(f"/api/v1/kg/autotuning/feedback?run_id={RUN_ID}", cookies=AUTH)
    assert r.status_code == 200
    d = r.json()
    assert d["summary"]["matured_count"] == 36
    assert d["shadow_run_id"] == SHADOW_RUN_ID
    monkeypatch.setattr(kq_api._pipeline, "autotuning_feedback", lambda conn, run_id: None)
    r = client.get(f"/api/v1/kg/autotuning/feedback?run_id={RUN_ID}", cookies=AUTH)
    assert r.status_code == 404
    r = client.get("/api/v1/kg/autotuning/feedback?run_id=bad", cookies=AUTH)
    assert r.status_code == 422


# ------------------------------------------------------------------ posts
def test_post_run_missing_idempotency_key_400(client: TestClient) -> None:
    r = client.post("/api/v1/kg/autotuning/run", json={"track_key": TRACK_A}, cookies=AUTH)
    assert r.status_code == 400
    assert r.json()["detail"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_post_run_unknown_track_422(client: TestClient) -> None:
    r = client.post(
        "/api/v1/kg/autotuning/run",
        json={"track_key": "XXX:nope:nope:day"},
        headers={"Idempotency-Key": "k1"},
        cookies=AUTH,
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "KQ_UNKNOWN_TRACK"


def test_post_run_completed_200_with_audit_id(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _SharedAuditStore()
    conns: list[_FakeWriteConn] = []

    def _factory() -> _FakeWriteConn:
        conn = _FakeWriteConn(store)
        conns.append(conn)
        return conn

    monkeypatch.setattr(kq_api, "write_conn", _factory)
    monkeypatch.setattr(
        kq_api._pipeline,
        "execute_autotuning",
        lambda conn, actor, idempotency_key, track_key: {
            "status": "COMPLETED",
            "track_key": track_key,
            "idempotency_key": idempotency_key,
            "run_id": RUN_ID,
            "shadow_run_id": SHADOW_RUN_ID,
            "target_status": "TARGET_MET",
        },
    )
    r = client.post(
        "/api/v1/kg/autotuning/run",
        json={"track_key": TRACK_A},
        headers={"Idempotency-Key": "fresh-key"},
        cookies=AUTH,
    )
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "COMPLETED"
    assert d["run_id"] == RUN_ID
    assert d["audit_id"]
    conn = conns[0]
    assert conn.committed is True
    # advisory lock is taken BEFORE the audit replay check
    assert conn.statements[0].startswith("SELECT pg_advisory_xact_lock")
    assert any(s.startswith("SELECT audit_id") for s in conn.statements)
    assert any(s.startswith("INSERT INTO epidemic_kg.kg_api_audit_log") for s in conn.statements)


def test_post_run_audit_replay_returns_same_run_id(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _SharedAuditStore()
    store.audit_rows[("/api/v1/kg/autotuning/run", "replay-key")] = "audit-1"
    monkeypatch.setattr(kq_api, "write_conn", lambda: _FakeWriteConn(store))
    called = []

    def _boom(conn: Any, **kwargs: Any) -> dict[str, Any]:
        called.append(True)
        raise AssertionError("execute_autotuning must not run on audit replay")

    monkeypatch.setattr(kq_api._pipeline, "execute_autotuning", _boom)
    r = client.post(
        "/api/v1/kg/autotuning/run",
        json={"track_key": TRACK_A},
        headers={"Idempotency-Key": "replay-key"},
        cookies=AUTH,
    )
    assert r.status_code == 200
    assert r.headers.get("Idempotent-Replay") == "true"
    d = r.json()
    assert d["idempotent_replay"] is True
    assert d["status"] == "IDEMPOTENT_REPLAY"
    assert d["run_id"] == RUN_ID  # deterministic read-back by idempotency key
    assert d["track_key"] == TRACK_A
    assert called == []


def test_post_run_concurrent_same_key_single_semantic_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """8 concurrent POSTs with one Idempotency-Key -> exactly one pipeline run;
    every caller gets a 200 carrying the same run_id."""
    store = _SharedAuditStore()
    monkeypatch.setattr(kq_api, "write_conn", lambda: _FakeWriteConn(store))
    calls: list[str] = []

    def _fake_execute(conn: Any, *, actor: str, idempotency_key: str, track_key: str) -> dict[str, Any]:
        calls.append(idempotency_key)
        return {
            "status": "COMPLETED",
            "track_key": track_key,
            "idempotency_key": idempotency_key,
            "run_id": RUN_ID,
            "shadow_run_id": SHADOW_RUN_ID,
            "target_status": "TARGET_MET",
        }

    monkeypatch.setattr(kq_api._pipeline, "execute_autotuning", _fake_execute)
    monkeypatch.setattr(
        kq_api._pipeline, "autotuning_overview", lambda conn, track_key: BASE_OVERVIEW
    )
    app = FastAPI()
    kq_api.install_autotuning_routes(app, _FakeEngine(ENGINE_ANSWERS), _authorized, _unauthorized, _json_response)
    client = TestClient(app)

    def _post(_: int) -> dict[str, Any]:
        r = client.post(
            "/api/v1/kg/autotuning/run",
            json={"track_key": TRACK_B},
            headers={"Idempotency-Key": "concurrent-key"},
            cookies=AUTH,
        )
        assert r.status_code == 200
        return r.json()

    with ThreadPoolExecutor(max_workers=8) as pool:
        bodies = list(pool.map(_post, range(8)))
    assert calls == ["concurrent-key"]  # exactly one semantic run
    for body in bodies:
        assert body["run_id"] == RUN_ID
    assert sum(1 for b in bodies if b.get("status") == "COMPLETED") == 1
    assert sum(1 for b in bodies if b.get("idempotent_replay") is True) == 7


def test_post_reconcile_happy_path_and_guards(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    r = client.post("/api/v1/kg/autotuning/reconcile", json={"run_id": RUN_ID}, cookies=AUTH)
    assert r.status_code == 400  # missing Idempotency-Key
    r = client.post(
        "/api/v1/kg/autotuning/reconcile",
        json={"run_id": "not-a-uuid"},
        headers={"Idempotency-Key": "k2"},
        cookies=AUTH,
    )
    assert r.status_code == 422
    store = _SharedAuditStore()
    monkeypatch.setattr(kq_api, "write_conn", lambda: _FakeWriteConn(store))
    monkeypatch.setattr(
        kq_api._pipeline,
        "reconcile_feedback",
        lambda conn, actor, idempotency_key, run_id: {
            "status": "COMPLETED",
            "run_id": run_id,
            "newly_matured": {"test_matured": 4},
            "retrain_triggered": False,
            "learning_cycle_id": "cycle-2",
        },
    )
    r = client.post(
        "/api/v1/kg/autotuning/reconcile",
        json={"run_id": RUN_ID},
        headers={"Idempotency-Key": "k2"},
        cookies=AUTH,
    )
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "COMPLETED"
    assert d["run_id"] == RUN_ID
    assert d["audit_id"]


def test_post_reconcile_audit_replay(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _SharedAuditStore()
    store.audit_rows[("/api/v1/kg/autotuning/reconcile", "rec-key")] = "audit-9"
    monkeypatch.setattr(kq_api, "write_conn", lambda: _FakeWriteConn(store))
    called = []
    monkeypatch.setattr(
        kq_api._pipeline,
        "reconcile_feedback",
        lambda conn, **kwargs: called.append(True) or {},
    )
    r = client.post(
        "/api/v1/kg/autotuning/reconcile",
        json={"run_id": RUN_ID},
        headers={"Idempotency-Key": "rec-key"},
        cookies=AUTH,
    )
    assert r.status_code == 200
    assert r.headers.get("Idempotent-Replay") == "true"
    d = r.json()
    assert d["idempotent_replay"] is True
    assert d["run_id"] == RUN_ID
    assert called == []
