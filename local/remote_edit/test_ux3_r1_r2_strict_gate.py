from __future__ import annotations

import concurrent.futures
import uuid
from datetime import date
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from epidemic_core.forecast_model_kg.dashboard_app import install_routes


DSN = (
    "postgresql+psycopg://REDACTED@/epidemic_openclaw"
    "?host=USER_HOME/projects/openclaw/runtime/postgres-run&port=55432"
)


def _client(authorized_value: bool = True) -> TestClient:
    engine = create_engine(DSN)
    app = FastAPI()

    def authorized(_request: Request) -> bool:
        return authorized_value

    def unauthorized() -> JSONResponse:
        return JSONResponse({"detail": "AUTH_REQUIRED"}, status_code=401)

    def json_response(value: object, status_code: int = 200) -> JSONResponse:
        return JSONResponse(value, status_code=status_code)

    install_routes(app, engine, authorized, unauthorized, json_response)
    return TestClient(app)


def _post_shadow(execution_id: str) -> dict[str, Any]:
    response = _client().post(
        "/api/v1/forecast-model-kg/ux3/demo-shadow-run"
        "?country=FIN&disease=hantavirus&cadence=week&metric=puumala_virus_reports"
        f"&execution_id={execution_id}"
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert isinstance(payload, dict)
    return payload


def test_r2_graph_layer_filters_return_distinct_sources() -> None:
    client = _client()
    payloads = {}
    for layer in ["forecast_guidance", "literature_evidence", "selection_trace", "all"]:
        response = client.get(
            f"/api/v1/forecast-model-kg/ux3/graph?search=hantavirus&layer={layer}&max_nodes=80&max_edges=160"
        )
        assert response.status_code == 200, response.text
        payloads[layer] = response.json()
        assert payloads[layer]["filter_contract"]["layer_filter_effective"] is True
        allowed_layers = {layer} if layer != "all" else {"forecast_guidance", "literature_evidence", "selection_trace"}
        assert {n["source_layer"] for n in payloads[layer]["nodes"]} <= allowed_layers
    assert payloads["forecast_guidance"]["returned_nodes"] > 0
    assert payloads["forecast_guidance"]["returned_edges"] > 0
    assert payloads["all"]["returned_nodes"] >= payloads["forecast_guidance"]["returned_nodes"]


def test_r2_graph_rejects_unknown_layer() -> None:
    response = _client().get("/api/v1/forecast-model-kg/ux3/graph?layer=decorative")
    assert response.status_code == 422
    assert response.json()["detail"] == "UNKNOWN_GRAPH_LAYER"


def test_r2_shadow_run_is_new_graph_derived_and_non_promoting() -> None:
    execution_id = "pytest_r2_" + uuid.uuid4().hex
    payload = _post_shadow(execution_id)
    run = payload["run"]
    trace = run["selection_trace"]
    values = payload["values"]
    assert run["mode"] == "SHADOW"
    assert payload["non_promotion_assertions"]["mode_equivalent"] == "DEMO_SHADOW"
    assert run["promotion_eligible"] is False
    assert run["active_pointer_changed"] is False
    assert run["trusted_rows_changed"] == 0
    assert trace["selection_mode"] == "STATIC_KG_GUIDANCE_WITH_REGISTERED_BASELINE_ADAPTERS"
    assert trace["advisory_rule_candidate_count"] == 0
    assert trace["graph_rule_candidate_count"] > 0
    assert trace["graph_derived_admitted_candidate_count"] > 0
    assert trace["graph_derived_executed_candidate_count"] > 0
    assert trace["graph_derived_fold_prediction_count"] > 0
    assert trace["pending_machine_claim_participation_count"] == 0
    assert trace["mentions_only_participation_count"] == 0
    assert trace["quarantined_claim_participation_count"] == 0
    assert trace["paper1_participation_count"] == 0
    assert {row["horizon"] for row in values if row["row_type"] == "BACKTEST"} == {1, 2, 3, 4}
    assert {row["horizon"] for row in values if row["row_type"] == "FUTURE"} == {1, 2, 3, 4}


def test_r2_future_week_intervals_and_interval_not_computed() -> None:
    payload = _post_shadow("pytest_r2_" + uuid.uuid4().hex)
    for row in payload["values"]:
        if row["row_type"] == "FUTURE":
            days = (date.fromisoformat(row["target_period_end"]) - date.fromisoformat(row["target_period_start"])).days
            assert days == 6
            assert row["lower_value"] is None
            assert row["upper_value"] is None


def test_r2_cdc_guard_fail_closed() -> None:
    response = _client().post(
        "/api/v1/forecast-model-kg/ux3/demo-shadow-run"
        "?country=USA&disease=influenza&cadence=week&metric=percent_weighted_ili"
        "&execution_id=pytest_cdc_guard"
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "CDC_R4F_UNAUTHORIZED_FOR_UX3"


def test_r2_unknown_cadence_guard() -> None:
    response = _client().post(
        "/api/v1/forecast-model-kg/ux3/demo-shadow-run"
        "?country=FIN&disease=hantavirus&cadence=century&metric=puumala_virus_reports"
        "&execution_id=pytest_bad_cadence"
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "UNSAFE_CADENCE"


def test_r2_unauthenticated_guard() -> None:
    response = _client(False).post(
        "/api/v1/forecast-model-kg/ux3/demo-shadow-run"
        "?country=FIN&disease=hantavirus&cadence=week&metric=puumala_virus_reports"
        "&execution_id=pytest_unauth"
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "AUTH_REQUIRED"


def test_r2_concurrent_new_key_before_zero_after_one() -> None:
    execution_id = "pytest_r2_concurrency_" + uuid.uuid4().hex
    idem_prefix = f"ux3r1r2:{execution_id}:%"
    engine = create_engine(DSN)
    with engine.connect() as connection:
        before_count = connection.execute(
            text("select count(*) from epidemic_kg.kg_forecast_shadow_runs where idempotency_key like :prefix"),
            {"prefix": idem_prefix},
        ).scalar_one()
    assert before_count == 0

    def call_once() -> tuple[int, str, str]:
        response = _client().post(
            "/api/v1/forecast-model-kg/ux3/demo-shadow-run"
            "?country=FIN&disease=hantavirus&cadence=week&metric=puumala_virus_reports"
            f"&execution_id={execution_id}"
        )
        payload = response.json()
        return response.status_code, payload["run"]["run_id"], payload["response_hash"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: call_once(), range(8)))

    assert {status for status, _run_id, _hash in results} == {200}
    assert len({_run_id for _status, _run_id, _hash in results}) == 1
    assert len({_hash for _status, _run_id, _hash in results}) == 1
    run_id = results[0][1]
    with engine.connect() as connection:
        after_count = connection.execute(
            text("select count(*) from epidemic_kg.kg_forecast_shadow_runs where idempotency_key like :prefix"),
            {"prefix": idem_prefix},
        ).scalar_one()
        duplicate_groups = connection.execute(
            text(
                """
                select count(*) from (
                  select target_period_start, horizon, row_type, count(*)
                  from epidemic_kg.kg_forecast_shadow_values
                  where run_id=:run_id
                  group by 1,2,3 having count(*) > 1
                ) s
                """
            ),
            {"run_id": run_id},
        ).scalar_one()
    assert after_count == 1
    assert duplicate_groups == 0


def test_r2_unknown_dataset_guard() -> None:
    response = _client().post(
        "/api/v1/forecast-model-kg/ux3/demo-shadow-run"
        "?country=PER&disease=influenza&cadence=week&metric=cases_new"
        "&execution_id=pytest_unknown_dataset"
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "UNKNOWN_UX3_R2_DEMO_DATASET"


def test_r2_dataset_sha_and_horizon_contract() -> None:
    payload = _post_shadow("pytest_r2_" + uuid.uuid4().hex)
    trace = payload["run"]["selection_trace"]
    assert len(trace["dataset_sha256"]) == 64
    assert trace["horizon_set"] == [1, 2, 3, 4]
    assert trace["baseline_guard"]["champion_model"]


def test_r2_candidate_provenance_complete() -> None:
    payload = _post_shadow("pytest_r2_" + uuid.uuid4().hex)
    for candidate in payload["run"]["selection_trace"]["candidate_models"]:
        assert candidate["candidate_id"]
        assert candidate["source_provider"]
        assert candidate["source_layer"] in {"forecast_guidance", "model_registry"}
        assert candidate["source_rule_ids"]
        assert candidate["adapter_key"]
        assert candidate["admission_status"] == "ADMITTED"
        assert candidate["actually_executed"] is True


def test_r2_selection_trace_graph_layer_after_run() -> None:
    payload = _post_shadow("pytest_r2_" + uuid.uuid4().hex)
    run_id = payload["run"]["run_id"]
    response = _client().get("/api/v1/forecast-model-kg/ux3/graph?search=hantavirus&layer=selection_trace")
    assert response.status_code == 200
    graph = response.json()
    assert graph["returned_nodes"] > 0
    assert any(node["raw_id"] == run_id for node in graph["nodes"])
    assert graph["returned_edges"] > 0


def test_r2_sequential_replay_same_payload_hash() -> None:
    execution_id = "pytest_r2_seq_" + uuid.uuid4().hex
    first = _post_shadow(execution_id)
    second = _post_shadow(execution_id)
    assert first["run"]["run_id"] == second["run"]["run_id"]
    assert first["response_hash"] == second["response_hash"]


def test_r2_parent_r1_run_not_reused() -> None:
    payload = _post_shadow("pytest_r2_" + uuid.uuid4().hex)
    assert payload["run"]["run_id"] != "d42eb8b5-0c5e-5b91-90b7-629979b3022b"


def test_r2_metric_rows_by_horizon_present() -> None:
    payload = _post_shadow("pytest_r2_" + uuid.uuid4().hex)
    candidates = payload["run"]["selection_trace"]["candidate_models"]
    for candidate in candidates:
        by_horizon = candidate["backtest_metrics"]["by_horizon"]
        assert {int(k) for k in by_horizon} == {1, 2, 3, 4}
        assert all(v["fold_count"] > 0 for v in by_horizon.values())


def test_r2_no_claim_or_paper1_participation() -> None:
    trace = _post_shadow("pytest_r2_" + uuid.uuid4().hex)["run"]["selection_trace"]
    assert trace["pending_machine_claim_participation_count"] == 0
    assert trace["mentions_only_participation_count"] == 0
    assert trace["quarantined_claim_participation_count"] == 0
    assert trace["paper1_participation_count"] == 0


def test_r2_values_have_no_duplicate_groups_for_new_run() -> None:
    payload = _post_shadow("pytest_r2_" + uuid.uuid4().hex)
    run_id = payload["run"]["run_id"]
    with create_engine(DSN).connect() as connection:
        duplicate_groups = connection.execute(
            text(
                """
                select count(*) from (
                  select target_period_start, horizon, row_type, count(*)
                  from epidemic_kg.kg_forecast_shadow_values
                  where run_id=:run_id
                  group by 1,2,3 having count(*) > 1
                ) s
                """
            ),
            {"run_id": run_id},
        ).scalar_one()
    assert duplicate_groups == 0


def test_r2_graph_all_layer_has_no_orphan_edges() -> None:
    response = _client().get("/api/v1/forecast-model-kg/ux3/graph?search=hantavirus&layer=all")
    assert response.status_code == 200
    graph = response.json()
    node_ids = {node["id"] for node in graph["nodes"]}
    for edge in graph["edges"]:
        assert edge["source_id"] in node_ids
        assert edge["target_id"] in node_ids
