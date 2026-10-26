# ruff: noqa: E501, B008
"""KG dual-dataset forecast feedback loop API.

Read endpoints go through the existing read-only SQLAlchemy engine; write
endpoints use the local unix-socket ``epidemic_app`` connection and the same
``Idempotency-Key`` + ``kg_api_audit_log`` replay contract as the other kg_r3c
write APIs. Models are resolved exclusively from the persisted
ExecutionContract / adapter registry; callers can only pass identifiers —
never code, SQL, paths or shell fragments.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import Body, FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import Engine, text

SCHEMA = "epidemic_kg"
AUDIT_ACTOR = "dashboard_user"

DECISION_CODES = {
    "USEFUL",
    "NOT_USEFUL",
    "EXPLANATION_CLEAR",
    "EXPLANATION_UNCLEAR",
    "DATA_QUALITY_CONCERN",
    "NEEDS_MORE_EVIDENCE",
}


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "__str__") and value.__class__.__module__ == "decimal":
        return float(value)
    return value


def _canonical_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(_jsonable(payload) or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def install_dual_loop_routes(
    app: FastAPI,
    engine: Engine,
    authorized: Callable[[Request], bool],
    unauthorized: Callable[[], Response],
    json_response: Callable[[Any, int], Response],
) -> None:
    from epidemic_core.kg_r3c.dashboard_app import write_conn

    def _replay(audit_id: str) -> JSONResponse:
        return JSONResponse(
            {"idempotent_replay": True, "audit_id": audit_id},
            status_code=200,
            headers={"Idempotent-Replay": "true"},
        )

    def _audit_replay(conn: Any, endpoint: str, idempotency_key: str | None) -> str | None:
        if not idempotency_key:
            return None
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT audit_id FROM {SCHEMA}.kg_api_audit_log WHERE endpoint=%s AND idempotency_key=%s",
                (endpoint, idempotency_key),
            )
            row = cur.fetchone()
        return str(row["audit_id"]) if row else None

    def _audit_record(
        conn: Any, endpoint: str, method: str, idempotency_key: str | None, request_hash: str, response_status: int
    ) -> str:
        audit_id = str(uuid.uuid4())
        with conn.cursor() as cur:
            cur.execute(
                f"""INSERT INTO {SCHEMA}.kg_api_audit_log
                    (audit_id, endpoint, method, actor, idempotency_key, request_hash, response_status)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (endpoint, idempotency_key) DO NOTHING""",
                (audit_id, endpoint, method, AUDIT_ACTOR, idempotency_key, request_hash, response_status),
            )
        return audit_id

    # ------------------------------------------------------------- reads
    @app.get("/api/v1/kg/forecast-eligible-series")
    def kg_dual_eligible_series(request: Request) -> Response:
        if not authorized(request):
            return unauthorized()
        from epidemic_core.forecast_model_kg.dual_loop import eligibility_verdict

        with engine.connect() as connection:
            rows = (
                connection.execute(
                    text(f"""
                    select s.country, s.disease, s.indicator, s.frequency, s.n_obs,
                           s.first_date, s.last_date, s.snapshot_sha256,
                           p.profile_id::text, p.profile_sha256,
                           r.recommendation_id::text, r.adapter_id, r.total_score,
                           r.parameter_confidence_score,
                           a.data_requirements
                    from {SCHEMA}.kg_data_snapshots s
                    join {SCHEMA}.kg_data_profiles p on p.snapshot_id = s.snapshot_id
                    left join {SCHEMA}.kg_recommendations r
                      on r.profile_id = p.profile_id and r.is_baseline = false and r.rank = 1
                    left join {SCHEMA}.kg_model_adapters a on a.adapter_id = r.adapter_id
                    order by s.country, s.disease, s.indicator
                """)
                )
                .mappings()
                .all()
            )
            live = (
                connection.execute(
                    text("""
                    select country_alpha3, disease_slug, metric_code, granularity,
                           count(*) filter (where value_numeric is not null)::int as n_valid,
                           count(*)::int as n_total
                    from epidemic.dashboard_history_series_v2
                    where latest_rank = 1
                    group by 1,2,3,4
                """)
                )
                .mappings()
                .all()
            )
        live_map = {(r["country_alpha3"], r["disease_slug"], r["metric_code"], r["granularity"]): r for r in live}
        items = []
        for r in rows:
            key = (r["country"], r["disease"], r["indicator"], r["frequency"])
            lv = live_map.get(key)
            min_obs = 12
            reqs = r.get("data_requirements") or {}
            if isinstance(reqs, dict) and reqs.get("min_obs"):
                min_obs = int(reqs["min_obs"])
            verdict = eligibility_verdict(
                {
                    "n_valid": (lv or {}).get("n_valid", r["n_obs"] or 0),
                    "coverage": ((lv or {}).get("n_valid", 0) or 0) / max((lv or {}).get("n_total", 1) or 1, 1),
                    "frequency": r["frequency"],
                    "trusted": True,
                    "adapter_min_obs": min_obs,
                }
            )
            items.append(
                {
                    **{
                        k: r[k]
                        for k in (
                            "country",
                            "disease",
                            "indicator",
                            "frequency",
                            "n_obs",
                            "first_date",
                            "last_date",
                            "snapshot_sha256",
                            "profile_id",
                            "profile_sha256",
                            "recommendation_id",
                            "adapter_id",
                            "total_score",
                            "parameter_confidence_score",
                        )
                    },
                    "eligibility": verdict["eligibility"],
                }
            )
        return json_response(
            _jsonable(
                {"items": items, "router_note": "eligibility is triage only; it never guarantees a numeric forecast"}
            ),
            200,
        )

    @app.get("/api/v1/kg/recommendations/{recommendation_id}")
    def kg_dual_recommendation_detail(request: Request, recommendation_id: str) -> Response:
        if not authorized(request):
            return unauthorized()
        if not _valid_uuid(recommendation_id):
            return json_response({"detail": "INVALID_ID"}, 422)
        with engine.connect() as connection:
            rec = (
                connection.execute(
                    text(f"""
                    select r.recommendation_id::text, r.profile_id::text, r.adapter_id,
                           r.bundle_id::text, r.total_score, r.parameter_confidence_score,
                           r.eligibility_status, s.country, s.disease, s.indicator, s.frequency,
                           s.snapshot_sha256, p.profile_sha256,
                           c.contract_id::text as execution_contract_id, c.status as contract_status
                    from {SCHEMA}.kg_recommendations r
                    join {SCHEMA}.kg_data_profiles p on p.profile_id = r.profile_id
                    join {SCHEMA}.kg_data_snapshots s on s.snapshot_id = p.snapshot_id
                    left join {SCHEMA}.kg_execution_contracts c on c.recommendation_id = r.recommendation_id
                    where r.recommendation_id = cast(:id as uuid)
                """),
                    {"id": recommendation_id},
                )
                .mappings()
                .first()
            )
            if not rec:
                return json_response({"detail": "RECOMMENDATION_NOT_FOUND"}, 404)
            runs = (
                connection.execute(
                    text(f"""
                    select run_id::text, status, model_name, cadence, data_through, created_at
                    from {SCHEMA}.kg_forecast_shadow_runs
                    where profile->>'recommendation_id' = :id
                    order by created_at desc
                """),
                    {"id": recommendation_id},
                )
                .mappings()
                .all()
            )
        return json_response(
            _jsonable(
                {
                    "recommendation": dict(rec),
                    "shadow_runs": [dict(r) for r in runs],
                    "score_note": "PRE_EXECUTION_EVIDENCE_SCORE measures evidence sufficiency, not forecast accuracy",
                }
            ),
            200,
        )

    @app.get("/api/v1/kg/shadow-executions")
    def kg_dual_shadow_list(request: Request, country: str | None = None, disease: str | None = None) -> Response:
        if not authorized(request):
            return unauthorized()
        with engine.connect() as connection:
            rows = (
                connection.execute(
                    text(f"""
                    select run_id::text, run_key, mode, status, disease_slug, country_alpha3,
                           metric_code, cadence, data_through, model_name, fallback_reason,
                           profile->>'recommendation_id' as recommendation_id,
                           profile->>'recommended_adapter' as recommended_adapter,
                           profile->>'dataset_sha256' as dataset_sha256,
                           profile->>'seed' as seed,
                           selection_trace->'champion'->>'champion_source' as champion_source,
                           selection_trace->'champion'->>'kg_model_status' as kg_model_status,
                           created_at
                    from {SCHEMA}.kg_forecast_shadow_runs
                    where idempotency_key like 'dual:%%'
                      and (cast(:country as text) is null or country_alpha3 = cast(:country as text))
                      and (cast(:disease as text) is null or disease_slug = cast(:disease as text))
                    order by created_at desc
                """),
                    {"country": country, "disease": disease},
                )
                .mappings()
                .all()
            )
        return json_response(_jsonable({"items": [dict(r) for r in rows]}), 200)

    def _load_run(connection: Any, run_id: str) -> Any:
        return (
            connection.execute(
                text(f"""
                select run_id::text, run_key, idempotency_key, mode, status, disease_slug,
                       country_alpha3, metric_code, cadence, data_through, model_name,
                       fallback_reason, promotion_eligible, active_pointer_changed,
                       profile, selection_trace, created_at
                from {SCHEMA}.kg_forecast_shadow_runs where run_id = cast(:id as uuid)
            """),
                {"id": run_id},
            )
            .mappings()
            .first()
        )

    @app.get("/api/v1/kg/shadow-executions/{run_id}")
    def kg_dual_shadow_detail(request: Request, run_id: str) -> Response:
        if not authorized(request):
            return unauthorized()
        if not _valid_uuid(run_id):
            return json_response({"detail": "INVALID_ID"}, 422)
        with engine.connect() as connection:
            run = _load_run(connection, run_id)
            if not run:
                return json_response({"detail": "SHADOW_RUN_NOT_FOUND"}, 404)
        return json_response(
            _jsonable(
                {"run": dict(run), "shadow_notice": "SHADOW run: not published, no promotion, no active-pointer change"}
            ),
            200,
        )

    @app.get("/api/v1/kg/shadow-executions/{run_id}/backtest")
    def kg_dual_shadow_backtest(request: Request, run_id: str) -> Response:
        if not authorized(request):
            return unauthorized()
        if not _valid_uuid(run_id):
            return json_response({"detail": "INVALID_ID"}, 422)
        with engine.connect() as connection:
            run = _load_run(connection, run_id)
            if not run:
                return json_response({"detail": "SHADOW_RUN_NOT_FOUND"}, 404)
            folds = (
                connection.execute(
                    text(f"""
                    select adapter_id, origin_index, origin_period, train_row_count,
                           train_sha256, model_artifact_sha256, status
                    from {SCHEMA}.kg_shadow_backtest_folds where run_id = cast(:id as uuid)
                    order by adapter_id, origin_index
                """),
                    {"id": run_id},
                )
                .mappings()
                .all()
            )
            metrics = (
                connection.execute(
                    text(f"""
                    select adapter_id, horizon, mae, rmse, wape, mase, deviance,
                           interval_coverage, interval_avg_width, forecast_row_count,
                           labeled_row_count, missing_label_count, metrics_sha256
                    from {SCHEMA}.kg_shadow_model_metrics where run_id = cast(:id as uuid)
                    order by adapter_id, horizon
                """),
                    {"id": run_id},
                )
                .mappings()
                .all()
            )
        return json_response(
            _jsonable(
                {
                    "run_id": run_id,
                    "folds": [dict(f) for f in folds],
                    "metrics": [dict(m) for m in metrics],
                    "champion": (run["selection_trace"] or {}).get("champion"),
                    "not_eligible": (run["selection_trace"] or {}).get("not_eligible"),
                }
            ),
            200,
        )

    @app.get("/api/v1/kg/shadow-executions/{run_id}/forecast")
    def kg_dual_shadow_forecast(request: Request, run_id: str) -> Response:
        if not authorized(request):
            return unauthorized()
        if not _valid_uuid(run_id):
            return json_response({"detail": "INVALID_ID"}, 422)
        with engine.connect() as connection:
            run = _load_run(connection, run_id)
            if not run:
                return json_response({"detail": "SHADOW_RUN_NOT_FOUND"}, 404)
            values = (
                connection.execute(
                    text(f"""
                    select value_id::text, target_period_start, horizon, yhat, lower_value,
                           upper_value, actual_value, is_observed, row_type, properties
                    from {SCHEMA}.kg_forecast_shadow_values where run_id = cast(:id as uuid)
                    order by target_period_start, horizon
                """),
                    {"id": run_id},
                )
                .mappings()
                .all()
            )
            history = (
                connection.execute(
                    text("""
                    select period_start, value_numeric
                    from epidemic.dashboard_history_series_v2
                    where country_alpha3 = :country and disease_slug = :disease
                      and metric_code = :metric and granularity = :granularity and latest_rank = 1
                    order by period_start
                """),
                    {
                        "country": run["country_alpha3"],
                        "disease": run["disease_slug"],
                        "metric": run["metric_code"],
                        "granularity": run["cadence"],
                    },
                )
                .mappings()
                .all()
            )
        return json_response(
            _jsonable(
                {
                    "run_id": run_id,
                    "data_through": run["data_through"],
                    "values": [dict(v) for v in values],
                    "history": [dict(h) for h in history],
                    "freshness": "STALE_HISTORICAL_REPLAY" if str(run["cadence"]) == "month" else "CURRENT_DEMO_SHADOW",
                }
            ),
            200,
        )

    @app.get("/api/v1/kg/shadow-executions/{run_id}/feedback")
    def kg_dual_shadow_feedback(request: Request, run_id: str) -> Response:
        if not authorized(request):
            return unauthorized()
        if not _valid_uuid(run_id):
            return json_response({"detail": "INVALID_ID"}, 422)
        with engine.connect() as connection:
            run = _load_run(connection, run_id)
            if not run:
                return json_response({"detail": "SHADOW_RUN_NOT_FOUND"}, 404)
            objective = (
                connection.execute(
                    text(f"""
                    select feedback_id::text, adapter_id, target_period, actual_value,
                           actual_revision, absolute_error, squared_error, interval_hit,
                           direction_correct, feedback_maturity, actual_available_at
                    from {SCHEMA}.kg_forecast_objective_feedback where run_id = cast(:id as uuid)
                    order by target_period, adapter_id
                """),
                    {"id": run_id},
                )
                .mappings()
                .all()
            )
            analyst = (
                connection.execute(
                    text(f"""
                    select feedback_id::text, actor, rating, decision_code, comment, created_at
                    from {SCHEMA}.kg_analyst_feedback where run_id = cast(:id as uuid)
                    order by created_at desc
                """),
                    {"id": run_id},
                )
                .mappings()
                .all()
            )
            cycles = (
                connection.execute(
                    text(f"""
                    select learning_cycle_id::text, cycle_type, status, matured_feedback_count,
                           evidence_sha256, created_at
                    from {SCHEMA}.kg_learning_cycles where run_id = cast(:id as uuid)
                    order by created_at
                """),
                    {"id": run_id},
                )
                .mappings()
                .all()
            )
        objective_rows = [dict(o) for o in objective]
        matured = len([o for o in objective_rows if o["feedback_maturity"] == "MATURED"])
        awaiting = len([o for o in objective_rows if o["feedback_maturity"] == "AWAITING_ACTUAL"])
        errors = [
            float(o["absolute_error"])
            for o in objective_rows
            if o["feedback_maturity"] == "MATURED" and o["absolute_error"] is not None
        ]
        hits = [o for o in objective_rows if o["feedback_maturity"] == "MATURED" and o["interval_hit"] is not None]
        return json_response(
            _jsonable(
                {
                    "run_id": run_id,
                    "objective_feedback": objective_rows,
                    "analyst_feedback": [dict(a) for a in analyst],
                    "learning_cycles": [dict(c) for c in cycles],
                    "summary": {
                        "matured_count": matured,
                        "awaiting_actual_count": awaiting,
                        "mean_absolute_error": (sum(errors) / len(errors)) if errors else None,
                        "interval_empirical_coverage": (len([h for h in hits if h["interval_hit"]]) / len(hits))
                        if hits
                        else None,
                    },
                }
            ),
            200,
        )

    @app.get("/api/v1/kg/learning-cycles/{learning_cycle_id}")
    def kg_dual_learning_cycle(request: Request, learning_cycle_id: str) -> Response:
        if not authorized(request):
            return unauthorized()
        if not _valid_uuid(learning_cycle_id):
            return json_response({"detail": "INVALID_ID"}, 422)
        with engine.connect() as connection:
            cycle = (
                connection.execute(
                    text(f"""
                    select learning_cycle_id::text, run_id::text, recommendation_id::text,
                           cycle_type, status, min_matured_feedback, matured_feedback_count,
                           evidence, evidence_sha256, created_at
                    from {SCHEMA}.kg_learning_cycles where learning_cycle_id = cast(:id as uuid)
                """),
                    {"id": learning_cycle_id},
                )
                .mappings()
                .first()
            )
            if not cycle:
                return json_response({"detail": "LEARNING_CYCLE_NOT_FOUND"}, 404)
            decision = (
                connection.execute(
                    text(f"""
                    select decision_id::text, champion_adapter_id, champion_source,
                           kg_model_status, reason, decided_by, promotion_applied, created_at
                    from {SCHEMA}.kg_champion_decisions where learning_cycle_id = cast(:id as uuid)
                """),
                    {"id": learning_cycle_id},
                )
                .mappings()
                .first()
            )
        return json_response(
            _jsonable(
                {
                    "learning_cycle": dict(cycle),
                    "champion_decision": dict(decision) if decision else None,
                    "learning_rule": "CONTROLLED_BATCH_LEARNING: reconcile -> evidence -> challenger backtest -> human decision; never auto-promote",
                }
            ),
            200,
        )

    @app.get("/api/v1/kg/shadow-executions/{run_id}/export.csv")
    def kg_dual_shadow_export_csv(request: Request, run_id: str) -> Response:
        if not authorized(request):
            return unauthorized()
        if not _valid_uuid(run_id):
            return json_response({"detail": "INVALID_ID"}, 422)
        with engine.connect() as connection:
            rows = (
                connection.execute(
                    text(f"""
                    select adapter_id, origin_period, target_period, horizon, yhat,
                           lower_bound, upper_bound, actual_value, abs_error,
                           interval_method, not_eligible, prediction_sha256
                    from {SCHEMA}.kg_shadow_backtest_predictions where run_id = cast(:id as uuid)
                    order by adapter_id, origin_period, horizon
                """),
                    {"id": run_id},
                )
                .mappings()
                .all()
            )
        header = "adapter_id,origin_period,target_period,horizon,yhat,lower_bound,upper_bound,actual_value,abs_error,interval_method,not_eligible,prediction_sha256"
        lines = [header]
        for r in rows:
            lines.append(
                ",".join(
                    "" if r[k] is None else str(r[k])
                    for k in (
                        "adapter_id",
                        "origin_period",
                        "target_period",
                        "horizon",
                        "yhat",
                        "lower_bound",
                        "upper_bound",
                        "actual_value",
                        "abs_error",
                        "interval_method",
                        "not_eligible",
                        "prediction_sha256",
                    )
                )
            )
        return Response(
            "\n".join(lines) + "\n",
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=shadow_backtest_{run_id}.csv"},
        )

    # ------------------------------------------------------------ writes
    @app.post("/api/v1/kg/shadow-executions")
    def kg_dual_shadow_execute(
        request: Request,
        payload: dict[str, Any] = Body(...),
        idempotency_key: str | None = Header(default=None),
    ) -> Response:
        if not authorized(request):
            return unauthorized()
        recommendation_id = str(payload.get("recommendation_id") or "").strip()
        if not _valid_uuid(recommendation_id):
            return json_response({"detail": "INVALID_RECOMMENDATION_ID"}, 422)
        idem = idempotency_key
        endpoint = "/api/v1/kg/shadow-executions"
        request_hash = _canonical_hash({"recommendation_id": recommendation_id})
        with write_conn() as conn:
            replay = _audit_replay(conn, endpoint, idem)
            if replay:
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT run_id FROM {SCHEMA}.kg_forecast_shadow_runs WHERE profile->>'recommendation_id'=%s ORDER BY created_at DESC LIMIT 1",
                        (recommendation_id,),
                    )
                    existing = cur.fetchone()
                return JSONResponse(
                    {
                        "idempotent_replay": True,
                        "audit_id": replay,
                        "status": "IDEMPOTENT_REPLAY",
                        "run_id": str(existing["run_id"]) if existing else None,
                    },
                    status_code=200,
                    headers={"Idempotent-Replay": "true"},
                )
            from epidemic_core.forecast_model_kg.dual_loop import execute_shadow_run

            try:
                result = execute_shadow_run(conn, recommendation_id, actor=AUDIT_ACTOR)
            except KeyError as exc:
                _audit_record(conn, endpoint, "POST", idem, request_hash, 404)
                return json_response({"detail": str(exc).split(":")[0]}, 404)
            status_code = 200 if result.get("status") in ("COMPLETED", "IDEMPOTENT_REPLAY") else 409
            audit_id = _audit_record(conn, endpoint, "POST", idem, request_hash, status_code)
            conn.commit()
        result = {k: v for k, v in result.items() if k not in ("metrics",)}
        return json_response(_jsonable({**result, "audit_id": audit_id}), status_code)

    @app.post("/api/v1/kg/shadow-executions/{run_id}/analyst-feedback")
    def kg_dual_analyst_feedback(
        request: Request,
        run_id: str,
        payload: dict[str, Any] = Body(...),
        idempotency_key: str | None = Header(default=None),
    ) -> Response:
        if not authorized(request):
            return unauthorized()
        if not _valid_uuid(run_id):
            return json_response({"detail": "INVALID_ID"}, 422)
        actor = str(payload.get("actor") or "").strip()
        decision_code = str(payload.get("decision_code") or "").strip().upper()
        comment = payload.get("comment")
        rating = payload.get("rating")
        if not actor or decision_code not in DECISION_CODES:
            return json_response({"detail": "INVALID_REQUEST", "allowed_decision_codes": sorted(DECISION_CODES)}, 422)
        if rating is not None and not (1 <= int(rating) <= 5):
            return json_response({"detail": "INVALID_RATING"}, 422)
        idem = idempotency_key
        endpoint = "/api/v1/kg/shadow-executions/{id}/analyst-feedback"
        request_hash = _canonical_hash({"run_id": run_id, "body": payload})
        with write_conn() as conn:
            replay = _audit_replay(conn, endpoint, idem)
            if replay:
                return _replay(replay)
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT run_id, profile->>'recommendation_id' as recommendation_id FROM {SCHEMA}.kg_forecast_shadow_runs WHERE run_id=%s",
                    (run_id,),
                )
                run = cur.fetchone()
                if not run:
                    _audit_record(conn, endpoint, "POST", idem, request_hash, 404)
                    return json_response({"detail": "SHADOW_RUN_NOT_FOUND"}, 404)
                comment_sha = hashlib.sha256(str(comment or "").encode("utf-8")).hexdigest()
                feedback_id = str(uuid.uuid4())
                cur.execute(
                    f"""INSERT INTO {SCHEMA}.kg_analyst_feedback
                        (feedback_id, run_id, recommendation_id, actor, rating, decision_code, comment, comment_sha256)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (run_id, actor, decision_code, comment_sha256) DO NOTHING
                        RETURNING feedback_id""",
                    (feedback_id, run_id, run["recommendation_id"], actor, rating, decision_code, comment, comment_sha),
                )
                row = cur.fetchone()
                created = row is not None
                if not created:
                    cur.execute(
                        f"SELECT feedback_id FROM {SCHEMA}.kg_analyst_feedback WHERE run_id=%s AND actor=%s AND decision_code=%s AND comment_sha256=%s",
                        (run_id, actor, decision_code, comment_sha),
                    )
                    row = cur.fetchone()
            audit_id = _audit_record(conn, endpoint, "POST", idem, request_hash, 200)
            conn.commit()
        return json_response(
            {
                "feedback_id": str(row["feedback_id"]),
                "run_id": run_id,
                "decision_code": decision_code,
                "created": created,
                "note": "analyst feedback never mutates observations and never triggers promotion",
                "audit_id": audit_id,
            },
            200,
        )

    @app.post("/api/v1/kg/reconciliation/run")
    def kg_dual_reconciliation_run(
        request: Request,
        payload: dict[str, Any] | None = Body(default=None),
        idempotency_key: str | None = Header(default=None),
    ) -> Response:
        if not authorized(request):
            return unauthorized()
        run_id = str((payload or {}).get("run_id") or "").strip() or None
        if run_id and not _valid_uuid(run_id):
            return json_response({"detail": "INVALID_ID"}, 422)
        idem = idempotency_key
        endpoint = "/api/v1/kg/reconciliation/run"
        request_hash = _canonical_hash({"run_id": run_id})
        with write_conn() as conn:
            replay = _audit_replay(conn, endpoint, idem)
            if replay:
                return _replay(replay)
            from epidemic_core.forecast_model_kg.dual_loop import reconcile_feedback

            result = reconcile_feedback(conn, run_id=run_id)
            audit_id = _audit_record(conn, endpoint, "POST", idem, request_hash, 200)
            conn.commit()
        return json_response(
            {
                **result,
                "run_id": run_id,
                "audit_id": audit_id,
                "note": "reconciliation reads official observations only; it never writes them",
            },
            200,
        )
