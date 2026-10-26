from __future__ import annotations

# ruff: noqa: I001

import concurrent.futures
from datetime import date

from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from epidemic_core.forecast_model_kg.dashboard_app import install_routes


DSN = (
    "postgresql+psycopg://REDACTED@/epidemic_openclaw"
    "?host=USER_HOME/projects/openclaw/runtime/postgres-run&port=55432"
)


def _client() -> TestClient:
    engine = create_engine(DSN)
    app = FastAPI()

    def authorized(_request: Request) -> bool:
        return True

    def unauthorized() -> JSONResponse:
        return JSONResponse({"detail": "AUTH_REQUIRED"}, status_code=401)

    def json_response(value: object, status_code: int = 200) -> JSONResponse:
        return JSONResponse(value, status_code=status_code)

    install_routes(
        app,
        engine,
        authorized,
        unauthorized,
        json_response,
    )
    return TestClient(app)


def test_ux3_r1_graph_endpoint_nonempty_and_closed() -> None:
    client = _client()
    response = client.get("/api/v1/forecast-model-kg/ux3/graph?search=influenza&max_nodes=60&max_edges=120")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["returned_nodes"] > 0
    assert payload["returned_edges"] > 0
    node_ids = {node["id"] for node in payload["nodes"]}
    assert len(node_ids) == len(payload["nodes"])
    edge_ids = {edge["id"] for edge in payload["edges"]}
    assert len(edge_ids) == len(payload["edges"])
    for edge in payload["edges"]:
        assert edge["source_id"] in node_ids
        assert edge["target_id"] in node_ids
        assert edge["source_layer"]
        assert edge["status"]


def test_ux3_r1_shadow_run_h1_h4_weekly_intervals_and_guards() -> None:
    client = _client()
    response = client.post(
        "/api/v1/forecast-model-kg/ux3/demo-shadow-run"
        "?country=FIN&disease=hantavirus&cadence=week&metric=puumala_virus_reports"
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    run = payload["run"]
    trace = run["selection_trace"]
    values = payload["values"]
    assert run["mode"] == "SHADOW"
    assert payload["non_promotion_assertions"]["mode_equivalent"] == "DEMO_SHADOW"
    assert run["promotion_eligible"] is False
    assert run["active_pointer_changed"] is False
    assert run["trusted_rows_changed"] == 0
    assert trace["horizon_set"] == [1, 2, 3, 4]
    assert trace["executed_candidate_count"] >= 2
    assert trace["advisory_rule_candidate_count"] > 0
    assert trace["pending_machine_claim_participation_count"] == 0
    assert trace.get("paper1_participation_count", 0) == 0
    backtest = [row for row in values if row["row_type"] == "BACKTEST"]
    future = [row for row in values if row["row_type"] == "FUTURE"]
    assert {row["horizon"] for row in backtest} == {1, 2, 3, 4}
    assert {row["horizon"] for row in future} == {1, 2, 3, 4}
    assert len(future) == 4
    for row in future:
        assert row["target_period_start"] != row["target_period_end"]
        # Weekly closed interval: start + 6 days.
        days = (date.fromisoformat(row["target_period_end"]) - date.fromisoformat(row["target_period_start"])).days
        assert days == 6


def test_ux3_r1_sequential_and_concurrent_idempotency() -> None:
    url = (
        "/api/v1/forecast-model-kg/ux3/demo-shadow-run"
        "?country=FIN&disease=hantavirus&cadence=week&metric=puumala_virus_reports"
    )
    first = _client().post(url)
    second = _client().post(url)
    assert first.status_code == 200
    assert second.status_code == 200
    first_payload = first.json()
    second_payload = second.json()
    assert first_payload["run"]["run_id"] == second_payload["run"]["run_id"]
    assert len(first_payload["values"]) == len(second_payload["values"])

    def call_once() -> tuple[int, str]:
        response = _client().post(url)
        return response.status_code, response.json()["run"]["run_id"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: call_once(), range(8)))
    assert {status for status, _run_id in results} == {200}
    assert len({_run_id for _status, _run_id in results}) == 1

    run_id = results[0][1]
    with create_engine(DSN).connect() as connection:
        duplicate_groups = connection.execute(
            text(
                """
                select count(*) from (
                  select target_period_start, horizon, row_type, count(*)
                  from epidemic_kg.kg_forecast_shadow_values
                  where run_id=:run_id
                  group by 1,2,3
                  having count(*) > 1
                ) s
                """
            ),
            {"run_id": run_id},
        ).scalar_one()
    assert duplicate_groups == 0


def test_ux3_r1_cdc_guard_fail_closed() -> None:
    response = _client().post(
        "/api/v1/forecast-model-kg/ux3/demo-shadow-run"
        "?country=USA&disease=influenza&cadence=week&metric=percent_weighted_ili"
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "CDC_R4F_UNAUTHORIZED_FOR_UX3"
