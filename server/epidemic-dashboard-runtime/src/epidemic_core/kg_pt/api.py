"""KG parameter-tuning + controlled self-learning demo API (kg_pt).

Read endpoints go through the existing read-only SQLAlchemy engine; the write
endpoint uses the kg_r3c ``write_conn()`` unix-socket connection and the same
``Idempotency-Key`` + ``kg_api_audit_log`` replay contract as the other KG
write APIs, plus a ``pg_advisory_xact_lock`` so concurrent same-key posts
serialize into exactly one semantic run. Everything here is Shadow-only: no
promotion, no active-pointer change, no trusted-table mutation.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Body, FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import Engine, text

from epidemic_core.kg_pt import DEMO_ADAPTER_ID, DEMO_KEY, DEMO_RECOMMENDATION_ID
from epidemic_core.kg_pt import pipeline as _pipeline

SCHEMA = "epidemic_kg"
AUDIT_ACTOR = "dashboard_user"
MAX_TRIAL_ROWS = 500

# Lazily resolved from epidemic_core.kg_r3c.dashboard_app (kept importable
# without psycopg / the runtime tree); tests monkeypatch this module attribute.
write_conn: Callable[[], Any] | None = None

LIMITATIONS_ZH = [
    "全部结果处于 Shadow 状态：未发布、未晋级、不影响任何正式预测（SHADOW_ONLY_NO_PROMOTION）。",
    "测试窗口的实际值来自历史数据回放（DEMO_HISTORICAL_REPLAY），不是实时到达的新数据。",
    "预测吻合度是基于 WAPE 换算的易读指标，不是分类准确率，也不代表未来保证准确。",
    "第二个学习周期之后没有剩余独立测试集（NO_REMAINING_HELDOUT），受控重训结果如实标注。",
    "本演示是决策支持工具，不构成医疗或公共卫生结论。",
]


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


def _resolve_write_conn() -> Callable[[], Any]:
    global write_conn
    if write_conn is None:
        from epidemic_core.kg_r3c.dashboard_app import write_conn as factory

        write_conn = factory
    return write_conn


class _ReadResult:
    """psycopg-style result surface over a SQLAlchemy mappings result."""

    def __init__(self, mappings: Any) -> None:
        self._mappings = mappings

    def fetchone(self) -> Any:
        return self._mappings.first()

    def fetchall(self) -> Any:
        return self._mappings.all()


class _ReadConn:
    """Adapt the read-only SQLAlchemy connection to the psycopg-ish
    ``conn.execute(sql, %s-params)`` surface the kg_pt pipeline helpers expect.
    ``exec_driver_sql`` passes the statement through unchanged, so ``%s``
    placeholders and ``::text`` casts reach the DBAPI driver verbatim."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def execute(self, sql: str, params: Any = ()) -> _ReadResult:
        result = self._connection.exec_driver_sql(sql, tuple(params or ()))
        return _ReadResult(result.mappings())


def _rows(connection: Any, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(r) for r in connection.execute(text(sql), params).mappings().all()]


def _row(connection: Any, sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
    row = connection.execute(text(sql), params).mappings().first()
    return dict(row) if row else None


def _reason_components_zh(
    rec: dict[str, Any] | None, contracts: list[dict[str, Any]], snapshot: dict[str, Any]
) -> list[str]:
    """Chinese-renderable reason components for the KG recommendation."""
    reasons: list[str] = []
    if rec:
        reasons.append(
            f"KG 推荐模型为 {rec.get('adapter_id')}：执行前证据总分 {rec.get('total_score')}/100，"
            f"参数可信度 {rec.get('parameter_confidence_score')}/100。"
        )
        reasons.append(
            "推荐依据：该模型在数据结构适配、疾病机制、国家场景、论文证据、参数可信度与执行准备度"
            "六个维度评分最高；评分明细见 recommendation_components（分数代表证据充分程度，不是预测准确率）。"
        )
    else:
        reasons.append("未在知识图谱中找到对应的推荐记录，无法给出推荐理由。")
    sources = sorted({str(c.get("parameter_source")) for c in contracts if c.get("parameter_source")})
    if "PAPER_EXTRACTED" in sources:
        reasons.append("部分参数标注为论文提取（PAPER_EXTRACTED），可追溯到论文原文证据。")
    else:
        reasons.append(
            "参数来源：KG 中没有这些参数的论文提取证据，默认值全部如实标注为实现默认值"
            "（IMPLEMENTATION_DEFAULT）；season_lag 由数据画像规则（DATA_PROFILE_RULE）按月度频率固定为 12。"
        )
    tunable = [str(c.get("parameter_name")) for c in contracts if c.get("tunable")]
    if tunable:
        reasons.append(f"可调参数：{('、'.join(tunable))}；搜索范围与安全约束见参数表。")
    if snapshot:
        freq_zh = {"day": "日", "week": "周", "month": "月"}.get(
            str((snapshot.get("demo_key") or "").split(":")[-1]), ""
        )
        reasons.append(
            f"数据画像：{snapshot.get('n_obs')} 条{freq_zh}度观测，"
            f"{snapshot.get('first_period')} 至 {snapshot.get('last_period')}；缺失值保持 NULL，从不填充。"
        )
    return reasons


def install_pt_routes(
    app: FastAPI,
    engine: Engine,
    authorized: Callable[[Request], bool],
    unauthorized: Callable[[], Response],
    json_response: Callable[[Any, int], Response],
) -> None:
    def _replay(audit_id: str) -> JSONResponse:
        return JSONResponse(
            {"idempotent_replay": True, "audit_id": audit_id, "status": "IDEMPOTENT_REPLAY"},
            status_code=200,
            headers={"Idempotent-Replay": "true"},
        )

    def _replay_with_ids(audit_id: str, demo_key: str) -> JSONResponse:
        """Audit-replay response enriched with the explicit run ids.

        The demo content is content-addressed: exactly one shadow run and one
        tuning run per cycle exist for a demo_key by construction, so the ids
        are read back through the same deterministic read path as the overview
        GET (``pipeline.pt_overview`` on the read-only engine) — never guessed
        via max(created_at). When multiple tuning runs are present they are
        selected by their ``cycle_index`` field (cycle 1 = initial tuning,
        cycle 2 = controlled retrain), which is unique per snapshot. If the
        overview read comes back empty or fails (edge: the audit row exists
        but the demo data was downgraded away), the slim body is returned.
        """
        body: dict[str, Any] = {"idempotent_replay": True, "audit_id": audit_id, "status": "IDEMPOTENT_REPLAY"}
        base = None
        try:
            with engine.connect() as connection:
                base = _pipeline.pt_overview(_ReadConn(connection), demo_key)
        except Exception:  # enrichment is best-effort; the slim body is the contract floor
            base = None
        if not base:
            return _replay(audit_id)
        snapshot = base.get("snapshot") or {}
        by_cycle = {r.get("cycle_index"): r for r in base.get("tuning_runs") or []}
        run1 = by_cycle.get(1) or {}
        run2 = by_cycle.get(2) or {}
        body.update(
            {
                "shadow_run_id": run1.get("shadow_run_id"),
                "tuning_run_id": run1.get("tuning_run_id"),
                "tuning_run_id_cycle2": run2.get("tuning_run_id"),
                "snapshot_sha256": snapshot.get("snapshot_sha256"),
                "split_sha256": run1.get("split_sha256"),
            }
        )
        return JSONResponse(_jsonable(body), status_code=200, headers={"Idempotent-Replay": "true"})

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

    def _enrich_overview(connection: Any, base: dict[str, Any]) -> dict[str, Any]:
        snapshot = base.get("snapshot") or {}
        tuning_runs = base.get("tuning_runs") or []
        run1 = tuning_runs[0] if tuning_runs else {}
        shadow_run_id = run1.get("shadow_run_id")
        split = None
        if run1.get("split_sha256"):
            split = _row(
                connection,
                f"""
                select train_start, train_end, validation_start, validation_end,
                       test_start, test_end, train_rows, validation_rows, test_rows, split_sha256
                from {SCHEMA}.kg_pt_temporal_splits where split_sha256 = :sha
                """,
                {"sha": run1["split_sha256"]},
            )
        contracts = _rows(
            connection,
            f"""
            select adapter_id, parameter_name, data_type, default_value, lower_bound, upper_bound,
                   transform, parameter_source, missing_reason, safety_constraint, tunable
            from {SCHEMA}.kg_pt_parameter_contracts where adapter_id = :adapter
            order by parameter_name
            """,
            {"adapter": run1.get("adapter_id") or DEMO_ADAPTER_ID},
        )
        rec = _row(
            connection,
            f"""
            select r.recommendation_id::text, r.adapter_id, r.total_score, r.parameter_confidence_score,
                   r.eligibility_status, s.country, s.disease, s.indicator, s.frequency
            from {SCHEMA}.kg_recommendations r
            join {SCHEMA}.kg_data_profiles p on p.profile_id = r.profile_id
            join {SCHEMA}.kg_data_snapshots s on s.snapshot_id = p.snapshot_id
            where r.recommendation_id = cast(:id as uuid)
            """,
            {"id": DEMO_RECOMMENDATION_ID},
        )
        components = _rows(
            connection,
            f"""
            select dimension, rule_id, raw_value, score, max_score, is_penalty, rationale
            from {SCHEMA}.kg_recommendation_components
            where recommendation_id = cast(:id as uuid)
            order by dimension, rule_id
            """,
            {"id": DEMO_RECOMMENDATION_ID},
        )
        validation_metrics: dict[str, Any] = {"trial0": None, "best_trial": None}
        if run1.get("tuning_run_id"):
            rows = _rows(
                connection,
                f"""
                select trial_id::text, trial_index, params, common_wape, coverage,
                       avg_interval_width, objective_value, gate_pass, failure_reason
                from {SCHEMA}.kg_pt_tuning_trials
                where tuning_run_id = cast(:trid as uuid)
                  and (trial_index = 0 or trial_id::text = cast(:best as text))
                order by trial_index
                """,
                {"trid": run1["tuning_run_id"], "best": run1.get("best_trial_id")},
            )
            for r in rows:
                if r["trial_index"] == 0:
                    validation_metrics["trial0"] = r
                elif r["trial_id"] == run1.get("best_trial_id"):
                    validation_metrics["best_trial"] = r
        feedback_summary: dict[str, Any] = {
            "matured_count": 0,
            "awaiting_count": 0,
            "superseded_count": 0,
            "mean_abs_error": None,
            "empirical_coverage": None,
        }
        learning_cycles: list[dict[str, Any]] = []
        champion_decisions: list[dict[str, Any]] = []
        if shadow_run_id:
            groups = _rows(
                connection,
                f"""
                select feedback_maturity, count(*)::int as n,
                       avg(absolute_error)::float as mean_abs_error,
                       avg(case when interval_hit then 1.0 else 0.0 end)
                         filter (where interval_hit is not null)::float as empirical_coverage
                from {SCHEMA}.kg_forecast_objective_feedback
                where run_id = cast(:rid as uuid)
                group by feedback_maturity
                """,
                {"rid": shadow_run_id},
            )
            by_maturity = {g["feedback_maturity"]: g for g in groups}
            matured = by_maturity.get("MATURED") or {}
            feedback_summary = {
                "matured_count": matured.get("n", 0),
                "awaiting_count": (by_maturity.get("AWAITING_ACTUAL") or {}).get("n", 0),
                "superseded_count": (by_maturity.get("SUPERSEDED_REVISION") or {}).get("n", 0),
                "mean_abs_error": matured.get("mean_abs_error"),
                "empirical_coverage": matured.get("empirical_coverage"),
            }
            learning_cycles = _rows(
                connection,
                f"""
                select learning_cycle_id::text, cycle_type, status, min_matured_feedback,
                       matured_feedback_count, evidence, created_at
                from {SCHEMA}.kg_learning_cycles where run_id = cast(:rid as uuid)
                order by created_at
                """,
                {"rid": shadow_run_id},
            )
            champion_decisions = _rows(
                connection,
                f"""
                select decision_id::text, learning_cycle_id::text, champion_adapter_id, champion_source,
                       kg_model_status, reason, promotion_applied, created_at
                from {SCHEMA}.kg_champion_decisions where run_id = cast(:rid as uuid)
                order by created_at
                """,
                {"rid": shadow_run_id},
            )
        evaluations = base.get("test_evaluations") or []
        tuned_eval = next((e for e in evaluations if e.get("is_tuned_model")), None)
        baselines = [e for e in evaluations if not e.get("is_tuned_model") and e.get("wape") is not None]
        best_baseline = min(baselines, key=lambda e: float(e["wape"])) if baselines else None
        improvement = None
        if tuned_eval and best_baseline and tuned_eval.get("wape") is not None and float(best_baseline["wape"]):
            improvement = (float(best_baseline["wape"]) - float(tuned_eval["wape"])) / float(best_baseline["wape"])
        test_comparison = {
            "tuned_wape": tuned_eval.get("wape") if tuned_eval else None,
            "tuned_agreement_pct": tuned_eval.get("agreement_pct") if tuned_eval else None,
            "best_baseline_adapter": best_baseline.get("adapter_id") if best_baseline else None,
            "best_baseline_wape": best_baseline.get("wape") if best_baseline else None,
            "improvement_vs_best_baseline": improvement,
        }
        return {
            **base,
            "split": split,
            "parameter_contracts": contracts,
            "recommendation": rec,
            "recommendation_components": components,
            "recommendation_reason_zh": _reason_components_zh(rec, contracts, snapshot),
            "validation_metrics": validation_metrics,
            "test_comparison": test_comparison,
            "feedback_summary": feedback_summary,
            "learning_cycles": learning_cycles,
            "champion_decisions": champion_decisions,
            "limitations": LIMITATIONS_ZH,
        }

    # ------------------------------------------------------------- reads
    @app.get("/api/v1/kg/pt/demo/overview")
    def kg_pt_demo_overview(request: Request, demo_key: str = DEMO_KEY) -> Response:
        if not authorized(request):
            return unauthorized()
        with engine.connect() as connection:
            base = _pipeline.pt_overview(_ReadConn(connection), demo_key)
            if base is None:
                return json_response({"detail": "PT_DEMO_NOT_FOUND"}, 404)
            payload = {"demo_key": demo_key, **_enrich_overview(connection, base)}
        return json_response(_jsonable(payload), 200)

    @app.get("/api/v1/kg/pt/demo/trials")
    def kg_pt_demo_trials(request: Request, tuning_run_id: str) -> Response:
        if not authorized(request):
            return unauthorized()
        if not _valid_uuid(tuning_run_id):
            return json_response({"detail": "INVALID_ID"}, 422)
        with engine.connect() as connection:
            trials = _pipeline.pt_trials(_ReadConn(connection), tuning_run_id)
        if not trials:
            return json_response({"detail": "PT_TUNING_RUN_NOT_FOUND"}, 404)
        return json_response(
            _jsonable(
                {
                    "tuning_run_id": tuning_run_id,
                    "trials": trials[:MAX_TRIAL_ROWS],
                    "truncated": len(trials) > MAX_TRIAL_ROWS,
                }
            ),
            200,
        )

    @app.get("/api/v1/kg/pt/demo/forecast")
    def kg_pt_demo_forecast(request: Request, run_id: str) -> Response:
        if not authorized(request):
            return unauthorized()
        if not _valid_uuid(run_id):
            return json_response({"detail": "INVALID_ID"}, 422)
        with engine.connect() as connection:
            run = _row(
                connection,
                f"""
                select run_id::text, profile->>'snapshot_sha256' as snapshot_sha256
                from {SCHEMA}.kg_forecast_shadow_runs where run_id = cast(:id as uuid)
                """,
                {"id": run_id},
            )
            if not run:
                return json_response({"detail": "PT_SHADOW_RUN_NOT_FOUND"}, 404)
            history: list[dict[str, Any]] = []
            if run.get("snapshot_sha256"):
                history = _rows(
                    connection,
                    f"""
                    select r.row_index, r.period_start, r.value_numeric
                    from {SCHEMA}.kg_pt_demo_snapshot_rows r
                    join {SCHEMA}.kg_pt_demo_snapshots s on s.snapshot_id = r.snapshot_id
                    where s.snapshot_sha256 = :sha
                    order by r.row_index
                    """,
                    {"sha": run["snapshot_sha256"]},
                )
            payload = _pipeline.pt_forecast(_ReadConn(connection), run_id)
        # Fill test-window actuals from the FROZEN snapshot rows (future stays null).
        actual_by_period = {str(h["period_start"]): h["value_numeric"] for h in history}
        for v in payload.get("values") or []:
            if v.get("row_type") == "TEST_REPLAY" and v.get("actual_value") is None:
                v["actual_value"] = actual_by_period.get(str(v.get("target_period_start")))
        payload["history"] = history
        payload["snapshot_sha256"] = run.get("snapshot_sha256")
        return json_response(_jsonable(payload), 200)

    # ------------------------------------------------------------ writes
    @app.post("/api/v1/kg/pt/demo/run")
    def kg_pt_demo_run(
        request: Request,
        payload: Annotated[dict[str, Any], Body(...)],
        idempotency_key: str | None = Header(default=None),
    ) -> Response:
        if not authorized(request):
            return unauthorized()
        idem = (idempotency_key or "").strip()
        if not idem:
            return json_response({"detail": "IDEMPOTENCY_KEY_REQUIRED"}, 400)
        demo_key = str((payload or {}).get("demo_key") or "").strip()
        if demo_key != DEMO_KEY:
            return json_response({"detail": "PT_UNKNOWN_DEMO_KEY"}, 422)
        endpoint = "/api/v1/kg/pt/demo/run"
        request_hash = _canonical_hash({"demo_key": demo_key})
        with _resolve_write_conn()() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"pt-demo-run:{idem}",))
            replay = _audit_replay(conn, endpoint, idem)
            if replay:
                return _replay_with_ids(replay, demo_key)
            result = _pipeline.execute_pt_demo(conn, actor=AUDIT_ACTOR, idempotency_key=idem)
            status_code = 200 if result.get("status") in ("COMPLETED", "IDEMPOTENT_REPLAY") else 409
            audit_id = _audit_record(conn, endpoint, "POST", idem, request_hash, status_code)
            conn.commit()
        result = {k: v for k, v in result.items() if k not in ("test_evaluations",)}
        return json_response(_jsonable({**result, "audit_id": audit_id}), status_code)
