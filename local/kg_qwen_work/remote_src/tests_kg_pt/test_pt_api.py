# ruff: noqa: E501
"""Pure API tests for kg_pt.api (no database).

A minimal FastAPI app is built in-process; ``install_pt_routes`` is wired with
a fake read engine (canned SQL answers), a monkeypatched ``write_conn`` (the
psycopg write seam) and monkeypatched ``epidemic_core.kg_pt.pipeline`` helpers.
authorized/unauthorized/json_response fakes mirror dashboard/app.py semantics
(cookie session -> 401 JSON).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from epidemic_core.kg_pt import DEMO_KEY
from epidemic_core.kg_pt import api as pt_api

SNAP_SHA = "s" * 64
TUNING_RUN_ID = "11111111-1111-1111-1111-111111111111"
BEST_TRIAL_ID = "22222222-2222-2222-2222-222222222222"
SHADOW_RUN_ID = "33333333-3333-3333-3333-333333333333"

BASE_OVERVIEW: dict[str, Any] = {
    "snapshot": {
        "snapshot_id": "snap-1",
        "demo_key": DEMO_KEY,
        "n_obs": 240,
        "first_period": date(2005, 1, 1),
        "last_period": date(2024, 12, 1),
        "data_through": date(2024, 12, 1),
        "missing_periods": [],
        "missing_semantics": "MISSING_VALUE_KEPT_NULL_NEVER_IMPUTED",
        "dataset_sha256": "d" * 64,
        "snapshot_sha256": SNAP_SHA,
    },
    "tuning_runs": [
        {
            "tuning_run_id": TUNING_RUN_ID,
            "cycle_index": 1,
            "status": "COMPLETED",
            "seed": 20260811,
            "max_trials": 24,
            "max_seconds": 600,
            "objective": "common_wape_with_complexity_penalty",
            "code_version": "kg_pt.v1",
            "best_trial_id": BEST_TRIAL_ID,
            "shadow_run_id": SHADOW_RUN_ID,
            "split_sha256": "p" * 64,
            "started_at": None,
            "completed_at": None,
        }
    ],
    "test_evaluations": [
        {
            "eval_id": "e-tuned",
            "adapter_id": "negbin_dglm.v1",
            "is_tuned_model": True,
            "mae": 5.0,
            "rmse": 6.0,
            "wape": 0.2,
            "smape": 0.19,
            "mase": 0.8,
            "coverage": 0.9,
            "avg_interval_width": 30.0,
            "agreement_pct": 80.0,
        },
        {
            "eval_id": "e-base",
            "adapter_id": "seasonal_naive",
            "is_tuned_model": False,
            "mae": 7.0,
            "rmse": 8.0,
            "wape": 0.3,
            "smape": 0.28,
            "mase": 1.1,
            "coverage": None,
            "avg_interval_width": None,
            "agreement_pct": 70.0,
        },
    ],
    "learning_cycles": [],
    "limitations": ["SHADOW_ONLY_NO_PROMOTION"],
}

ENGINE_ANSWERS: list[tuple[str, list[dict[str, Any]]]] = [
    (
        "from epidemic_kg.kg_pt_temporal_splits",
        [
            {
                "train_start": date(2005, 1, 1),
                "train_end": date(2018, 12, 1),
                "validation_start": date(2019, 1, 1),
                "validation_end": date(2021, 12, 1),
                "test_start": date(2022, 1, 1),
                "test_end": date(2024, 12, 1),
                "train_rows": 168,
                "validation_rows": 36,
                "test_rows": 36,
                "split_sha256": "p" * 64,
            }
        ],
    ),
    (
        "from epidemic_kg.kg_pt_parameter_contracts",
        [
            {
                "adapter_id": "negbin_dglm.v1",
                "parameter_name": "discount",
                "data_type": "float",
                "default_value": 0.95,
                "lower_bound": 0.8,
                "upper_bound": 0.999,
                "transform": "none",
                "parameter_source": "IMPLEMENTATION_DEFAULT",
                "missing_reason": "NO_PAPER_EVIDENCE_IN_KG",
                "safety_constraint": "clip to [0.8, 0.999]",
                "tunable": True,
            }
        ],
    ),
    (
        "from epidemic_kg.kg_recommendation_components",
        [
            {
                "dimension": "A_data_structure_fit",
                "rule_id": "A3_LENGTH",
                "raw_value": "240",
                "score": 15.0,
                "max_score": 15.0,
                "is_penalty": False,
                "rationale": "n_obs sufficient",
            }
        ],
    ),
    (
        "from epidemic_kg.kg_recommendations r",
        [
            {
                "recommendation_id": "c219de41-ea2e-58d9-86cd-339b03213401",
                "adapter_id": "negbin_dglm.v1",
                "total_score": 80.88,
                "parameter_confidence_score": 75.0,
                "eligibility_status": "ELIGIBLE_RECOMMENDATION",
                "country": "AUT",
                "disease": "legionella",
                "indicator": "legionnaires_reported_cases",
                "frequency": "month",
            }
        ],
    ),
    (
        "from epidemic_kg.kg_pt_tuning_trials",
        [
            {
                "trial_id": "t0",
                "trial_index": 0,
                "params": {"discount": 0.95},
                "common_wape": 0.31,
                "coverage": 0.8,
                "avg_interval_width": 40.0,
                "objective_value": 0.31,
                "gate_pass": True,
                "failure_reason": None,
            },
            {
                "trial_id": BEST_TRIAL_ID,
                "trial_index": 7,
                "params": {"discount": 0.9},
                "common_wape": 0.25,
                "coverage": 0.85,
                "avg_interval_width": 38.0,
                "objective_value": 0.2501,
                "gate_pass": True,
                "failure_reason": None,
            },
        ],
    ),
    (
        "from epidemic_kg.kg_forecast_objective_feedback",
        [
            {"feedback_maturity": "MATURED", "n": 36, "mean_abs_error": 5.0, "empirical_coverage": 0.9},
            {"feedback_maturity": "AWAITING_ACTUAL", "n": 4, "mean_abs_error": None, "empirical_coverage": None},
            {"feedback_maturity": "SUPERSEDED_REVISION", "n": 36, "mean_abs_error": None, "empirical_coverage": None},
        ],
    ),
    (
        "from epidemic_kg.kg_learning_cycles",
        [
            {
                "learning_cycle_id": "cycle-1",
                "cycle_type": "PARAMETER_TUNING_EVALUATION",
                "status": "COMPLETED",
                "min_matured_feedback": 1,
                "matured_feedback_count": 36,
                "evidence": {"best_params": {"discount": 0.9}},
                "created_at": "2026-08-11T00:00:00+00:00",
            }
        ],
    ),
    (
        "from epidemic_kg.kg_champion_decisions",
        [
            {
                "decision_id": "dec-1",
                "learning_cycle_id": "cycle-1",
                "champion_adapter_id": "seasonal_naive",
                "champion_source": "EMPIRICAL_BASELINE_OVERRIDE",
                "kg_model_status": "EXECUTED_NOT_CHAMPION",
                "reason": "BASELINE_BETTER_OR_IMPROVEMENT_BELOW_1PCT",
                "promotion_applied": False,
                "created_at": "2026-08-11T00:00:00+00:00",
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
    def __init__(self, answers: list[tuple[str, list[dict[str, Any]]]]) -> None:
        self._answers = answers

    def connect(self) -> _FakeConnection:
        return _FakeConnection(self._answers)


class _FakeCursor:
    def __init__(self, conn: _FakeWriteConn) -> None:
        self._conn = conn

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def execute(self, sql: str, params: Any = None) -> None:
        self._conn.statements.append(" ".join(str(sql).split()))

    def fetchone(self) -> dict[str, Any] | None:
        last = self._conn.statements[-1] if self._conn.statements else ""
        if last.startswith("SELECT audit_id"):
            return self._conn.replay_row
        return None


class _FakeWriteConn:
    def __init__(self, replay_row: dict[str, Any] | None = None) -> None:
        self.replay_row = replay_row
        self.statements: list[str] = []
        self.committed = False

    def __enter__(self) -> _FakeWriteConn:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def commit(self) -> None:
        self.committed = True


def _authorized(request: Request) -> bool:
    return request.cookies.get("session") == "test-session"


def _unauthorized() -> JSONResponse:
    return JSONResponse({"detail": "UNAUTHORIZED"}, status_code=401)


def _json_response(payload: Any, status: int) -> JSONResponse:
    return JSONResponse(payload, status_code=status)


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(pt_api._pipeline, "pt_overview", lambda conn, demo_key: BASE_OVERVIEW)
    monkeypatch.setattr(
        pt_api._pipeline,
        "pt_trials",
        lambda conn, tuning_run_id: [{"trial_id": "t0", "trial_index": 0, "common_wape": 0.31, "gate_pass": True}],
    )
    monkeypatch.setattr(
        pt_api._pipeline,
        "pt_forecast",
        lambda conn, run_id: {
            "run_id": run_id,
            "values": [
                {
                    "target_period_start": date(2024, 12, 1),
                    "horizon": 1,
                    "yhat": 55.0,
                    "lower_value": 40.0,
                    "upper_value": 70.0,
                    "actual_value": None,
                    "row_type": "TEST_REPLAY",
                    "properties": {},
                },
                {
                    "target_period_start": date(2025, 1, 1),
                    "horizon": 1,
                    "yhat": 56.0,
                    "lower_value": 41.0,
                    "upper_value": 71.0,
                    "actual_value": None,
                    "row_type": "FUTURE",
                    "properties": {},
                },
            ],
            "limitations": [],
        },
    )
    app = FastAPI()
    pt_api.install_pt_routes(app, _FakeEngine(ENGINE_ANSWERS), _authorized, _unauthorized, _json_response)
    return TestClient(app)


AUTH = {"session": "test-session"}


def test_unauthenticated_get_401(client: TestClient) -> None:
    r = client.get("/api/v1/kg/pt/demo/overview")
    assert r.status_code == 401
    assert r.json()["detail"] == "UNAUTHORIZED"


def test_unauthenticated_post_401(client: TestClient) -> None:
    r = client.post("/api/v1/kg/pt/demo/run", json={"demo_key": DEMO_KEY})
    assert r.status_code == 401


def test_overview_200_expected_keys(client: TestClient) -> None:
    r = client.get("/api/v1/kg/pt/demo/overview", cookies=AUTH)
    assert r.status_code == 200
    d = r.json()
    for key in (
        "demo_key",
        "snapshot",
        "split",
        "tuning_runs",
        "parameter_contracts",
        "recommendation",
        "recommendation_components",
        "recommendation_reason_zh",
        "validation_metrics",
        "test_evaluations",
        "test_comparison",
        "feedback_summary",
        "learning_cycles",
        "champion_decisions",
        "limitations",
    ):
        assert key in d, key
    assert d["demo_key"] == DEMO_KEY
    assert d["snapshot"]["n_obs"] == 240
    assert d["validation_metrics"]["trial0"]["trial_index"] == 0
    assert d["validation_metrics"]["best_trial"]["trial_id"] == BEST_TRIAL_ID
    assert d["feedback_summary"]["matured_count"] == 36
    assert d["feedback_summary"]["awaiting_count"] == 4
    assert d["feedback_summary"]["superseded_count"] == 36
    assert d["test_comparison"]["best_baseline_adapter"] == "seasonal_naive"
    assert d["test_comparison"]["improvement_vs_best_baseline"] == pytest.approx((0.3 - 0.2) / 0.3)
    assert d["champion_decisions"][0]["promotion_applied"] is False
    assert any("Shadow" in s for s in d["limitations"])
    assert any("WAPE" in s for s in d["recommendation_reason_zh"] + d["limitations"])


def test_overview_404_when_empty(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pt_api._pipeline, "pt_overview", lambda conn, demo_key: None)
    r = client.get("/api/v1/kg/pt/demo/overview", cookies=AUTH)
    assert r.status_code == 404
    assert r.json()["detail"] == "PT_DEMO_NOT_FOUND"


def test_trials_200_and_404(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    r = client.get(f"/api/v1/kg/pt/demo/trials?tuning_run_id={TUNING_RUN_ID}", cookies=AUTH)
    assert r.status_code == 200
    d = r.json()
    assert d["tuning_run_id"] == TUNING_RUN_ID
    assert d["trials"][0]["trial_index"] == 0
    assert d["truncated"] is False
    monkeypatch.setattr(pt_api._pipeline, "pt_trials", lambda conn, tuning_run_id: [])
    r2 = client.get(f"/api/v1/kg/pt/demo/trials?tuning_run_id={TUNING_RUN_ID}", cookies=AUTH)
    assert r2.status_code == 404
    assert r2.json()["detail"] == "PT_TUNING_RUN_NOT_FOUND"


def test_forecast_200_fills_test_actuals(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    answers = ENGINE_ANSWERS + [
        (
            "from epidemic_kg.kg_forecast_shadow_runs",
            [{"run_id": SHADOW_RUN_ID, "snapshot_sha256": SNAP_SHA}],
        ),
        (
            "from epidemic_kg.kg_pt_demo_snapshot_rows",
            [
                {"row_index": 239, "period_start": date(2024, 12, 1), "value_numeric": 60.0},
                {"row_index": 238, "period_start": date(2024, 11, 1), "value_numeric": 58.0},
            ],
        ),
    ]
    app = FastAPI()
    pt_api.install_pt_routes(app, _FakeEngine(answers), _authorized, _unauthorized, _json_response)
    c = TestClient(app)
    r = c.get(f"/api/v1/kg/pt/demo/forecast?run_id={SHADOW_RUN_ID}", cookies=AUTH)
    assert r.status_code == 200
    d = r.json()
    assert d["run_id"] == SHADOW_RUN_ID
    assert d["snapshot_sha256"] == SNAP_SHA
    assert len(d["history"]) == 2
    test_cell = next(v for v in d["values"] if v["row_type"] == "TEST_REPLAY")
    future_cell = next(v for v in d["values"] if v["row_type"] == "FUTURE")
    assert test_cell["actual_value"] == 60.0  # filled from the frozen snapshot rows
    assert future_cell["actual_value"] is None  # future stays unknown


def test_forecast_404_unknown_run(client: TestClient) -> None:
    r = client.get(f"/api/v1/kg/pt/demo/forecast?run_id={SHADOW_RUN_ID}", cookies=AUTH)
    assert r.status_code == 404
    assert r.json()["detail"] == "PT_SHADOW_RUN_NOT_FOUND"


def test_post_missing_idempotency_key_400(client: TestClient) -> None:
    r = client.post("/api/v1/kg/pt/demo/run", json={"demo_key": DEMO_KEY}, cookies=AUTH)
    assert r.status_code == 400
    assert r.json()["detail"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_post_unknown_demo_key_422(client: TestClient) -> None:
    r = client.post(
        "/api/v1/kg/pt/demo/run",
        json={"demo_key": "XXX:nope:nope:week"},
        headers={"Idempotency-Key": "k1"},
        cookies=AUTH,
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "PT_UNKNOWN_DEMO_KEY"


def test_post_audit_replay_returns_header(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_conn = _FakeWriteConn(replay_row={"audit_id": "audit-1"})
    monkeypatch.setattr(pt_api, "write_conn", lambda: fake_conn)
    called = []

    def _boom(conn: Any, **kwargs: Any) -> dict[str, Any]:
        called.append(True)
        raise AssertionError("execute_pt_demo must not run on audit replay")

    monkeypatch.setattr(pt_api._pipeline, "execute_pt_demo", _boom)
    r = client.post(
        "/api/v1/kg/pt/demo/run",
        json={"demo_key": DEMO_KEY},
        headers={"Idempotency-Key": "replay-key"},
        cookies=AUTH,
    )
    assert r.status_code == 200
    assert r.headers.get("Idempotent-Replay") == "true"
    d = r.json()
    assert d["idempotent_replay"] is True
    assert d["status"] == "IDEMPOTENT_REPLAY"
    # explicit content-addressed run ids (selected by cycle_index, no created_at guessing)
    assert d["shadow_run_id"] == SHADOW_RUN_ID
    assert d["tuning_run_id"] == TUNING_RUN_ID
    assert "tuning_run_id_cycle2" in d
    assert d["snapshot_sha256"] == SNAP_SHA
    assert d["split_sha256"] == "p" * 64
    assert called == []
    assert any("pg_advisory_xact_lock" in s for s in fake_conn.statements)


def test_post_audit_replay_falls_back_to_slim_body(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Edge: audit row exists but the overview read is empty (downgraded data)
    -> slim replay body, still 200 + Idempotent-Replay header."""
    fake_conn = _FakeWriteConn(replay_row={"audit_id": "audit-2"})
    monkeypatch.setattr(pt_api, "write_conn", lambda: fake_conn)
    monkeypatch.setattr(pt_api._pipeline, "pt_overview", lambda conn, demo_key: None)
    r = client.post(
        "/api/v1/kg/pt/demo/run",
        json={"demo_key": DEMO_KEY},
        headers={"Idempotency-Key": "replay-key-orphan"},
        cookies=AUTH,
    )
    assert r.status_code == 200
    assert r.headers.get("Idempotent-Replay") == "true"
    d = r.json()
    assert d == {"idempotent_replay": True, "audit_id": "audit-2", "status": "IDEMPOTENT_REPLAY"}


def test_post_completed_200_with_audit_id(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_conn = _FakeWriteConn()
    monkeypatch.setattr(pt_api, "write_conn", lambda: fake_conn)
    monkeypatch.setattr(
        pt_api._pipeline,
        "execute_pt_demo",
        lambda conn, actor, idempotency_key: {
            "status": "COMPLETED",
            "demo_key": DEMO_KEY,
            "idempotency_key": idempotency_key,
            "tuning_run_id": TUNING_RUN_ID,
            "shadow_run_id": SHADOW_RUN_ID,
        },
    )
    r = client.post(
        "/api/v1/kg/pt/demo/run",
        json={"demo_key": DEMO_KEY},
        headers={"Idempotency-Key": "fresh-key"},
        cookies=AUTH,
    )
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "COMPLETED"
    assert d["tuning_run_id"] == TUNING_RUN_ID
    assert d["audit_id"]
    assert fake_conn.committed is True
    # advisory lock is taken BEFORE the audit replay check
    assert fake_conn.statements[0].startswith("SELECT pg_advisory_xact_lock")
    assert any(s.startswith("SELECT audit_id") for s in fake_conn.statements)
    assert any(s.startswith("INSERT INTO epidemic_kg.kg_api_audit_log") for s in fake_conn.statements)
