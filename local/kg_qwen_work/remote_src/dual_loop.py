"""Dual-dataset recommendation -> forecast -> feedback closed loop.

Implements the controlled execution path for KG-recommended models:

    series load -> rolling-origin backtest (recommended model + baselines on
    identical folds) -> per-horizon metrics -> empirical champion selection ->
    immutable objective feedback -> learning cycle -> Shadow persistence.

Safety invariants enforced here:
  * training rows for a fold only ever come from periods <= origin;
  * missing observations stay NULL and are never zero-filled or interpolated;
  * predictions are non-negative; baselines without the required lag return
    NULL with an explicit not-eligible reason;
  * every run is idempotent via a deterministic idempotency key;
  * nothing here mutates trusted observations, historical runs, promotions
    or active pointers.
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta
from typing import Any

from epidemic_core.kg_r3c.execution import (
    BASELINE_IDS,
    EXECUTION_IMPL_VERSION,
    TrainingRow,
    baseline_not_eligible,
    fit_model,
    nb_deviance,
    predict_model,
    sha256_json,
    sha256_text,
    stable_uuid,
)

SCHEMA = "epidemic_kg"
HORIZONS = [1, 2, 3, 4]
LOOP_VERSION = "kg_dual_loop.v1"


class _TupleConn:
    """Force tuple rows regardless of the connection's default row factory
    (the dashboard write_conn uses dict_row). A psycopg cursor returns itself
    from execute(), so callers can keep using .execute(...).fetchone()."""

    def __init__(self, conn: Any):
        self._conn = conn

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        cur = self._conn.cursor(row_factory=self._tuple_row())
        return cur.execute(sql, params)

    @staticmethod
    def _tuple_row() -> Any:
        from psycopg.rows import tuple_row

        return tuple_row


def _as_tuple_conn(conn: Any) -> Any:
    if hasattr(conn, "cursor"):
        try:
            return _TupleConn(conn)
        except Exception:  # noqa: BLE001
            return conn
    return conn


DECISION_CODES = (
    "USEFUL",
    "NOT_USEFUL",
    "EXPLANATION_CLEAR",
    "EXPLANATION_UNCLEAR",
    "DATA_QUALITY_CONCERN",
    "NEEDS_MORE_EVIDENCE",
)

MATURITY_AWAITING = "AWAITING_ACTUAL"
MATURITY_MATURED = "MATURED"
MATURITY_SUPERSEDED = "SUPERSEDED_REVISION"


# ------------------------------------------------------------------ time util
def period_add(period: date, frequency: str, n: int) -> date:
    if frequency == "week":
        return period + timedelta(weeks=n)
    if frequency == "month":
        month = period.month - 1 + n
        year = period.year + month // 12
        return date(year, month % 12 + 1, 1)
    raise ValueError(f"UNSUPPORTED_FREQUENCY:{frequency}")


def period_offset_between(start: date, end: date, frequency: str) -> int:
    """Number of periods from start to end (end = start + n periods)."""
    n = 0
    cur = start
    while cur < end and n < 5000:
        cur = period_add(cur, frequency, 1)
        n += 1
    return n


# ------------------------------------------------------------------ series io
def load_series(conn: Any, country: str, disease: str, metric: str, granularity: str) -> list[TrainingRow]:
    rows = conn.execute(
        """
        select observation_id::text, period_start, period_end, value_numeric, revision
        from epidemic.dashboard_history_series_v2
        where country_alpha3 = %s and disease_slug = %s and metric_code = %s
          and granularity = %s and latest_rank = 1
        order by period_start
        """,
        (country, disease, metric, granularity),
    ).fetchall()
    return [
        TrainingRow(
            period_start=r[1],
            period_end=r[2],
            value=None if r[3] is None else float(r[3]),
            observation_id=r[0],
            revision=int(r[4] or 1),
        )
        for r in rows
    ]


def dataset_sha256(rows: list[TrainingRow], country: str, disease: str, metric: str, granularity: str) -> str:
    lines = [
        f"{r.observation_id}|{country}|{disease}|{granularity}|{metric}|"
        f"{r.period_start.isoformat()}|{r.period_end.isoformat() if r.period_end else ''}|"
        f"{'' if r.value is None else repr(float(r.value))}|{r.revision}"
        for r in rows
    ]
    return str(sha256_text("\n".join(lines) + "\n"))


# ------------------------------------------------------------------- origins
def default_origin_offsets(frequency: str) -> list[int]:
    if frequency == "month":
        return [48, 44, 40, 36, 32, 28, 24, 20, 16, 12, 8, 4]
    return [24, 20, 16, 12, 8, 4]


def compute_origin_indices(n_rows: int, offsets: list[int]) -> list[int]:
    """Origin index = last index - offset; targets origin+1..origin+4 must stay
    inside the series (offset >= max horizon by construction)."""
    origins = []
    for off in offsets:
        idx = (n_rows - 1) - off
        if idx >= 8 and idx + max(HORIZONS) <= n_rows - 1:
            origins.append(idx)
    return origins


# ------------------------------------------------------------------- metrics
def _mase_denominator(train_values: list[float]) -> float | None:
    diffs = [abs(b - a) for a, b in zip(train_values, train_values[1:], strict=False)]
    if not diffs:
        return None
    denom = sum(diffs) / len(diffs)
    return denom if denom > 0 else None


def compute_metrics(preds: list[dict[str, Any]], mase_denom: float | None) -> dict[int, dict[str, Any]]:
    """Per-horizon metrics plus overall (horizon 0)."""
    out: dict[int, dict[str, Any]] = {}
    for horizon in [0] + HORIZONS:
        rows = [p for p in preds if horizon == 0 or p["horizon"] == horizon]
        labeled = [p for p in rows if p["actual_value"] is not None and p["yhat"] is not None]
        missing = len([p for p in rows if p["actual_value"] is None])
        n = len(labeled)
        if n == 0:
            out[horizon] = {
                "horizon": horizon,
                "mae": None,
                "rmse": None,
                "wape": None,
                "mase": None,
                "deviance": None,
                "interval_coverage": None,
                "interval_avg_width": None,
                "forecast_row_count": len(rows),
                "labeled_row_count": 0,
                "missing_label_count": missing,
            }
            continue
        abs_e = [abs(p["yhat"] - p["actual_value"]) for p in labeled]
        sq_e = [(p["yhat"] - p["actual_value"]) ** 2 for p in labeled]
        denom = sum(abs(p["actual_value"]) for p in labeled)
        mae = sum(abs_e) / n
        devs = [p["deviance"] for p in labeled if p.get("deviance") is not None]
        hits = [
            1.0
            for p in labeled
            if p["lower_bound"] is not None
            and p["upper_bound"] is not None
            and p["lower_bound"] <= p["actual_value"] <= p["upper_bound"]
        ]
        widths = [
            p["upper_bound"] - p["lower_bound"]
            for p in labeled
            if p["lower_bound"] is not None and p["upper_bound"] is not None
        ]
        out[horizon] = {
            "horizon": horizon,
            "mae": round(mae, 6),
            "rmse": round(math.sqrt(sum(sq_e) / n), 6),
            "wape": round(sum(abs_e) / denom, 6) if denom > 0 else None,
            "mase": round(mae / mase_denom, 6) if mase_denom else None,
            "deviance": round(sum(devs) / len(devs), 6) if devs else None,
            "interval_coverage": round(sum(hits) / n, 6),
            "interval_avg_width": round(sum(widths) / len(widths), 6) if widths else None,
            "forecast_row_count": len(rows),
            "labeled_row_count": n,
            "missing_label_count": missing,
        }
    return out


# ------------------------------------------------------------------ backtest
def run_backtest(
    rows: list[TrainingRow], models: dict[str, dict[str, Any]], origin_indices: list[int], frequency: str, seed: int
) -> dict[str, Any]:
    """Execute every model on identical rolling origins.

    models: adapter_id -> {"parameters": dict}
    Returns {"folds": [...], "predictions": [...], "metrics": {adapter: {...}},
             "artifacts": {adapter: {origin: FittedModelArtifact}}}
    """
    folds: list[dict[str, Any]] = []
    predictions: list[dict[str, Any]] = []
    per_model_preds: dict[str, list[dict[str, Any]]] = {m: [] for m in models}
    artifacts: dict[str, dict[int, Any]] = {m: {} for m in models}
    not_eligible: dict[str, str] = {}

    for origin_idx in origin_indices:
        origin_row = rows[origin_idx]
        train_rows = rows[: origin_idx + 1]  # feature_max_period <= origin, hard boundary
        assert all(r.period_start <= origin_row.period_start for r in train_rows)
        train_values = [r.value for r in train_rows if r.value is not None]
        for adapter_id, spec in models.items():
            fitted = fit_model(
                adapter_id, train_rows, spec.get("parameters") or {}, seed, origin_row.period_start, frequency
            )
            artifacts[adapter_id][origin_idx] = fitted
            ne = baseline_not_eligible(fitted)
            if ne:
                not_eligible[adapter_id] = ne
            preds = predict_model(fitted, HORIZONS, origin_row.period_start)
            folds.append(
                {
                    "adapter_id": adapter_id,
                    "origin_index": origin_idx,
                    "origin_period": origin_row.period_start.isoformat(),
                    "train_row_count": len(train_rows),
                    "train_sha256": fitted.train_sha256,
                    "model_artifact_sha256": fitted.artifact_sha256,
                    "status": ne or "OK",
                }
            )
            for p in preds:
                target_period = period_add(origin_row.period_start, frequency, p["horizon"])
                target_idx = origin_idx + p["horizon"]
                actual_row = rows[target_idx] if target_idx < len(rows) else None
                # hard leakage gate: target must align with the real next period
                if actual_row is not None and actual_row.period_start != target_period:
                    actual_row = None  # gap in series -> label stays missing
                actual = actual_row.value if actual_row else None
                deviance = None
                if actual is not None and p["yhat"] is not None:
                    shape = fitted.state.get("nb_shape") if adapter_id == "negbin_dglm.v1" else None
                    deviance = nb_deviance(actual, p["yhat"], shape)
                direction_correct = None
                if actual is not None and p["yhat"] is not None and train_values:
                    base = train_values[-1]
                    direction_correct = bool((actual - base == 0) or ((actual - base) > 0) == ((p["yhat"] - base) > 0))
                row = {
                    "adapter_id": adapter_id,
                    "origin_index": origin_idx,
                    "origin_period": origin_row.period_start.isoformat(),
                    "target_period": target_period.isoformat(),
                    "horizon": p["horizon"],
                    "yhat": p["yhat"],
                    "lower_bound": p["lower_bound"],
                    "upper_bound": p["upper_bound"],
                    "interval_level": p["interval_level"],
                    "interval_method": p.get("interval_method"),
                    "actual_value": actual,
                    "actual_observation_id": actual_row.observation_id if actual_row else None,
                    "actual_revision": actual_row.revision if actual_row else None,
                    "deviance": deviance,
                    "direction_correct": direction_correct,
                    "model_artifact_sha256": fitted.artifact_sha256,
                    "not_eligible": p.get("not_eligible"),
                }
                predictions.append(row)
                per_model_preds[adapter_id].append(row)

    metrics: dict[str, dict[int, dict[str, Any]]] = {}
    for adapter_id in models:
        # MASE denominator per model uses full-series in-sample naive (documented);
        # fold-level denominators are implied by identical folds.
        all_train_values = [r.value for r in rows if r.value is not None]
        metrics[adapter_id] = compute_metrics(per_model_preds[adapter_id], _mase_denominator(all_train_values))
    return {
        "folds": folds,
        "predictions": predictions,
        "metrics": metrics,
        "artifacts": artifacts,
        "not_eligible": not_eligible,
    }


def select_champion(
    metrics: dict[str, dict[int, dict[str, Any]]], recommended_adapter: str, predictions: list[dict[str, Any]]
) -> dict[str, Any]:
    """Champion by overall WAPE on the common labeled sample (rows where the
    recommended model and the compared model both produced labeled forecasts)."""
    rec_rows = {
        (p["origin_index"], p["horizon"]): p
        for p in predictions
        if p["adapter_id"] == recommended_adapter and p["yhat"] is not None and p["actual_value"] is not None
    }

    def common_wape(adapter_id: str) -> tuple[float | None, int]:
        errors, actuals = 0.0, 0.0
        n = 0
        for p in predictions:
            if p["adapter_id"] != adapter_id or p["yhat"] is None or p["actual_value"] is None:
                continue
            if (p["origin_index"], p["horizon"]) not in rec_rows:
                continue
            errors += abs(p["yhat"] - p["actual_value"])
            actuals += abs(p["actual_value"])
            n += 1
        if n == 0 or actuals == 0:
            return None, n
        return errors / actuals, n

    rec_wape, rec_n = common_wape(recommended_adapter)
    ranking = []
    best_baseline = None
    for adapter_id in BASELINE_IDS:
        wape, n = common_wape(adapter_id)
        ranking.append(
            {"adapter_id": adapter_id, "common_wape": None if wape is None else round(wape, 6), "common_rows": n}
        )
        if wape is not None and (best_baseline is None or wape < best_baseline["common_wape"]):
            best_baseline = {"adapter_id": adapter_id, "common_wape": wape, "common_rows": n}
    ranking.append(
        {
            "adapter_id": recommended_adapter,
            "common_wape": None if rec_wape is None else round(rec_wape, 6),
            "common_rows": rec_n,
            "recommended": True,
        }
    )

    if rec_wape is not None and (best_baseline is None or rec_wape < best_baseline["common_wape"]):
        return {
            "champion_adapter_id": recommended_adapter,
            "champion_source": "KG_RECOMMENDED_MODEL",
            "kg_model_status": "EXECUTED_CHAMPION",
            "champion_reason": "KG_RECOMMENDED_MODEL_BEST_COMMON_WAPE",
            "comparison": ranking,
        }
    if best_baseline is not None:
        return {
            "champion_adapter_id": best_baseline["adapter_id"],
            "champion_source": "EMPIRICAL_BASELINE_OVERRIDE",
            "kg_model_status": "EXECUTED_NOT_CHAMPION",
            "champion_reason": "BASELINE_BETTER_COMMON_WAPE",
            "comparison": ranking,
        }
    return {
        "champion_adapter_id": recommended_adapter,
        "champion_source": "KG_RECOMMENDED_MODEL",
        "kg_model_status": "EXECUTED_NO_COMMON_COMPARISON",
        "champion_reason": "NO_BASELINE_COMMON_SAMPLE",
        "comparison": ranking,
    }


# ------------------------------------------------------------- identity types
def load_recommendation_context(conn: Any, recommendation_id: str) -> dict[str, Any]:
    rec = conn.execute(
        """
        select r.recommendation_id::text, r.run_id::text, r.profile_id::text,
               r.adapter_id, r.bundle_id::text, r.total_score, r.parameter_confidence_score,
               r.eligibility_status,
               s.country, s.disease, s.indicator, s.frequency,
               s.snapshot_id::text, s.snapshot_sha256, s.n_obs, s.first_date, s.last_date,
               p.profile_sha256,
               run.graph_version_id::text, run.policy_id
        from epidemic_kg.kg_recommendations r
        join epidemic_kg.kg_data_profiles p on p.profile_id = r.profile_id
        join epidemic_kg.kg_data_snapshots s on s.snapshot_id = p.snapshot_id
        join epidemic_kg.kg_recommendation_runs run on run.run_id = r.run_id
        where r.recommendation_id = %s
        """,
        (recommendation_id,),
    ).fetchone()
    if not rec:
        raise KeyError(f"RECOMMENDATION_NOT_FOUND:{recommendation_id}")
    cols = [
        "recommendation_id",
        "run_id",
        "profile_id",
        "adapter_id",
        "bundle_id",
        "total_score",
        "parameter_confidence_score",
        "eligibility_status",
        "country",
        "disease",
        "indicator",
        "frequency",
        "snapshot_id",
        "snapshot_sha256",
        "n_obs",
        "first_date",
        "last_date",
        "profile_sha256",
        "graph_version_id",
        "policy_id",
    ]
    ctx = dict(zip(cols, rec, strict=True))
    contract = conn.execute(
        "select contract_id::text, contract_sha256, status, contract "
        "from epidemic_kg.kg_execution_contracts where recommendation_id = %s "
        "order by created_at desc limit 1",
        (recommendation_id,),
    ).fetchone()
    if not contract:
        raise KeyError(f"EXECUTION_CONTRACT_NOT_FOUND:{recommendation_id}")
    ctx["execution_contract_id"] = contract[0]
    ctx["contract_sha256"] = contract[1]
    ctx["contract_status"] = contract[2]
    bundle = conn.execute(
        "select parameters, bundle_sha256 from epidemic_kg.kg_parameter_bundles where bundle_id = %s",
        (ctx["bundle_id"],),
    ).fetchone()
    params: dict[str, Any] = {}
    if bundle and bundle[0]:
        for p in bundle[0]:
            params[p["name"]] = p["value"]
    ctx["parameters"] = params
    ctx["bundle_sha256"] = bundle[1] if bundle else None
    # graph-version identity gate: the bound version must either be PUBLISHED or
    # be reattested by a PUBLISHED version with identical document/assertion sets.
    bound = conn.execute(
        "select graph_version_id::text, document_set_hash, assertion_set_hash, status "
        "from epidemic_kg.kg_graph_versions where graph_version_id = %s",
        (ctx["graph_version_id"],),
    ).fetchone()
    published = conn.execute(
        "select graph_version_id::text, document_set_hash, assertion_set_hash, status "
        "from epidemic_kg.kg_graph_versions where status = 'PUBLISHED'",
    ).fetchall()
    identity_ok = False
    identity_detail: dict[str, Any] = {"bound": bound[0] if bound else None}
    if bound:
        for pub in published:
            if pub[1] == bound[1] and pub[2] == bound[2]:
                identity_ok = True
                identity_detail["published_equivalent"] = pub[0]
                break
    ctx["graph_identity_ok"] = identity_ok
    ctx["graph_identity_detail"] = identity_detail
    return ctx


# -------------------------------------------------------------- run execution
def execute_shadow_run(
    conn: Any, recommendation_id: str, actor: str = "dual_loop_runner", seed: int = 20260810
) -> dict[str, Any]:
    """Execute the full closed loop for one recommendation. Idempotent: the
    same recommendation + contract + dataset + implementation version always
    resolves to the same run_id and never rewrites a historical run."""
    conn = _as_tuple_conn(conn)
    ctx = load_recommendation_context(conn, recommendation_id)
    if not ctx["graph_identity_ok"]:
        return {
            "status": "BLOCKED",
            "code": "KG_DUAL_LOOP_BLOCKED_GRAPH_VERSION_IDENTITY",
            "detail": ctx["graph_identity_detail"],
        }
    rows = load_series(conn, ctx["country"], ctx["disease"], ctx["indicator"], ctx["frequency"])
    if not rows:
        return {"status": "BLOCKED", "code": "NO_VALID_OBSERVATION"}
    dsha = dataset_sha256(rows, ctx["country"], ctx["disease"], ctx["indicator"], ctx["frequency"])
    idem = f"dual:{recommendation_id}:{ctx['contract_sha256']}:{dsha}:{EXECUTION_IMPL_VERSION}:{LOOP_VERSION}"
    run_id = str(stable_uuid("dual-shadow-run", idem))

    existing = conn.execute(
        "select run_id::text, status, profile, selection_trace from epidemic_kg.kg_forecast_shadow_runs "
        "where idempotency_key = %s",
        (idem,),
    ).fetchone()
    if existing:
        return {
            "status": "IDEMPOTENT_REPLAY",
            "run_id": existing[0],
            "idempotency_key": idem,
            "context": _ctx_brief(ctx, dsha),
        }

    recommended = ctx["adapter_id"]
    models: dict[str, dict[str, Any]] = {recommended: {"parameters": ctx["parameters"]}}
    for b in BASELINE_IDS:
        models[b] = {"parameters": {}}

    offsets = default_origin_offsets(ctx["frequency"])
    origins = compute_origin_indices(len(rows), offsets)
    bt = run_backtest(rows, models, origins, ctx["frequency"], seed)
    champion = select_champion(bt["metrics"], recommended, bt["predictions"])

    # future H1-H4 from the final origin (issue = last observed period)
    final_origin = len(rows) - 1
    issue_period = rows[final_origin].period_start
    champion_adapter = champion["champion_adapter_id"]
    fitted_final = fit_model(
        champion_adapter,
        rows,
        ctx["parameters"] if champion_adapter == recommended else {},
        seed,
        issue_period,
        ctx["frequency"],
    )
    future_preds = predict_model(fitted_final, HORIZONS, issue_period)
    data_through = rows[-1].period_start

    profile_payload = {
        "profile_id": ctx["profile_id"],
        "snapshot_id": ctx["snapshot_id"],
        "snapshot_sha256": ctx["snapshot_sha256"],
        "profile_sha256": ctx["profile_sha256"],
        "dataset_sha256": dsha,
        "recommendation_id": recommendation_id,
        "execution_contract_id": ctx["execution_contract_id"],
        "contract_sha256": ctx["contract_sha256"],
        "parameter_bundle_id": ctx["bundle_id"],
        "bundle_sha256": ctx["bundle_sha256"],
        "seed": seed,
        "impl_version": EXECUTION_IMPL_VERSION,
        "loop_version": LOOP_VERSION,
        "origin_count": len(origins),
        "origin_offsets": offsets,
        "recommended_adapter": recommended,
        "preexecution_score": float(ctx["total_score"]),
        "parameter_confidence": float(ctx["parameter_confidence_score"]),
        "score_note": "PRE_EXECUTION_EVIDENCE_SCORE is evidence sufficiency, not forecast accuracy",
        "graph_identity": ctx["graph_identity_detail"],
    }
    run_key = (
        f"dual-loop:{ctx['country']}:{ctx['disease']}:{ctx['indicator']}:{ctx['frequency']}:{recommendation_id[:8]}"
    )
    status = "COMPLETED"
    conn.execute(
        """
        insert into epidemic_kg.kg_forecast_shadow_runs
            (run_id, run_key, idempotency_key, mode, status, disease_slug, country_alpha3,
             metric_code, cadence, data_through, model_name, fallback_reason,
             promotion_eligible, active_pointer_changed, trusted_rows_changed,
             profile, selection_trace)
        values (%s,%s,%s,'SHADOW',%s,%s,%s,%s,%s,%s,%s,%s,false,false,0,%s::jsonb,%s::jsonb)
        on conflict (idempotency_key) do nothing
        """,
        (
            run_id,
            run_key,
            idem,
            status,
            ctx["disease"],
            ctx["country"],
            ctx["indicator"],
            ctx["frequency"],
            data_through,
            champion_adapter,
            champion["champion_reason"] if champion["champion_source"] != "KG_RECOMMENDED_MODEL" else None,
            json.dumps(profile_payload, default=str),
            json.dumps(
                {"champion": champion, "not_eligible": bt["not_eligible"], "shadow_notice": "SHADOW_ONLY_NO_PROMOTION"},
                default=str,
            ),
        ),
    )

    _persist_artifacts(conn, run_id, bt, fitted_final)
    _persist_folds_predictions_metrics(conn, run_id, bt)
    _persist_shadow_values(
        conn, run_id, ctx, bt, champion_adapter, future_preds, issue_period, data_through, dsha, seed, fitted_final
    )
    feedback = _persist_objective_feedback(conn, run_id, bt, future_preds, issue_period, ctx, rows)
    cycle = _persist_learning_cycle(conn, run_id, ctx, bt, champion, feedback)

    return {
        "status": status,
        "run_id": run_id,
        "idempotency_key": idem,
        "context": _ctx_brief(ctx, dsha),
        "origin_count": len(origins),
        "champion": champion,
        "metrics": {a: m[0] for a, m in bt["metrics"].items()},
        "not_eligible": bt["not_eligible"],
        "future_forecast_count": len(future_preds),
        "feedback": feedback,
        "learning_cycle": cycle,
    }


def _ctx_brief(ctx: dict[str, Any], dsha: str) -> dict[str, Any]:
    return {
        "country": ctx["country"],
        "disease": ctx["disease"],
        "indicator": ctx["indicator"],
        "frequency": ctx["frequency"],
        "profile_id": ctx["profile_id"],
        "snapshot_sha256": ctx["snapshot_sha256"],
        "dataset_sha256": dsha,
        "recommendation_id": ctx["recommendation_id"],
        "execution_contract_id": ctx["execution_contract_id"],
        "recommended_adapter": ctx["adapter_id"],
    }


def _persist_artifacts(conn: Any, run_id: str, bt: dict[str, Any], fitted_final: Any) -> None:
    seen: dict[str, Any] = {}
    for by_origin in bt["artifacts"].values():
        for art in by_origin.values():
            seen[art.artifact_sha256] = art
    seen[fitted_final.artifact_sha256] = fitted_final
    for art in seen.values():
        conn.execute(
            """
            insert into epidemic_kg.kg_shadow_model_artifacts
                (artifact_id, run_id, adapter_id, adapter_version, seed, state,
                 train_row_count, train_sha256, artifact_sha256)
            values (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
            on conflict (artifact_sha256) do nothing
            """,
            (
                str(stable_uuid("artifact", art.artifact_sha256)),
                run_id,
                art.adapter_id,
                art.adapter_version,
                art.seed,
                json.dumps(art.state, default=str),
                art.train_row_count,
                art.train_sha256,
                art.artifact_sha256,
            ),
        )


def _persist_folds_predictions_metrics(conn: Any, run_id: str, bt: dict[str, Any]) -> None:
    fold_ids: dict[tuple[str, int], str] = {}
    for f in bt["folds"]:
        fold_id = str(stable_uuid("fold", f"{run_id}:{f['adapter_id']}:{f['origin_period']}"))
        fold_ids[(f["adapter_id"], f["origin_index"])] = fold_id
        conn.execute(
            """
            insert into epidemic_kg.kg_shadow_backtest_folds
                (fold_id, run_id, adapter_id, origin_index, origin_period, train_row_count,
                 train_sha256, model_artifact_sha256, status)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (run_id, adapter_id, origin_period) do nothing
            """,
            (
                fold_id,
                run_id,
                f["adapter_id"],
                f["origin_index"],
                f["origin_period"],
                f["train_row_count"],
                f["train_sha256"],
                f["model_artifact_sha256"],
                f["status"],
            ),
        )
    for p in bt["predictions"]:
        psha = sha256_json(
            {
                "run_id": run_id,
                **{
                    k: p[k]
                    for k in (
                        "adapter_id",
                        "origin_period",
                        "target_period",
                        "horizon",
                        "yhat",
                        "lower_bound",
                        "upper_bound",
                        "actual_value",
                    )
                },
            }
        )
        conn.execute(
            """
            insert into epidemic_kg.kg_shadow_backtest_predictions
                (prediction_id, fold_id, run_id, adapter_id, origin_index, origin_period,
                 target_period, horizon, yhat, lower_bound, upper_bound, interval_level,
                 interval_method, actual_value, actual_observation_id, actual_revision,
                 abs_error, squared_error, deviance, direction_correct,
                 model_artifact_sha256, not_eligible, prediction_sha256)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (run_id, adapter_id, origin_period, horizon) do nothing
            """,
            (
                str(stable_uuid("bt-pred", psha)),
                fold_ids[(p["adapter_id"], p["origin_index"])],
                run_id,
                p["adapter_id"],
                p["origin_index"],
                p["origin_period"],
                p["target_period"],
                p["horizon"],
                p["yhat"],
                p["lower_bound"],
                p["upper_bound"],
                p["interval_level"],
                p["interval_method"],
                p["actual_value"],
                p["actual_observation_id"],
                p["actual_revision"],
                None if p["actual_value"] is None or p["yhat"] is None else abs(p["yhat"] - p["actual_value"]),
                None if p["actual_value"] is None or p["yhat"] is None else (p["yhat"] - p["actual_value"]) ** 2,
                p["deviance"],
                p["direction_correct"],
                p["model_artifact_sha256"],
                p["not_eligible"],
                psha,
            ),
        )
    for adapter_id, by_horizon in bt["metrics"].items():
        for horizon, m in by_horizon.items():
            msha = sha256_json({"run_id": run_id, "adapter_id": adapter_id, **m})
            conn.execute(
                """
                insert into epidemic_kg.kg_shadow_model_metrics
                    (metric_id, run_id, adapter_id, horizon, mae, rmse, wape, mase,
                     deviance, interval_coverage, interval_avg_width, forecast_row_count,
                     labeled_row_count, missing_label_count, metrics_sha256)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict (run_id, adapter_id, horizon) do nothing
                """,
                (
                    str(stable_uuid("metrics", msha)),
                    run_id,
                    adapter_id,
                    horizon,
                    m["mae"],
                    m["rmse"],
                    m["wape"],
                    m["mase"],
                    m["deviance"],
                    m["interval_coverage"],
                    m["interval_avg_width"],
                    m["forecast_row_count"],
                    m["labeled_row_count"],
                    m["missing_label_count"],
                    msha,
                ),
            )


def _persist_shadow_values(
    conn: Any,
    run_id: str,
    ctx: dict[str, Any],
    bt: dict[str, Any],
    champion_adapter: str,
    future_preds: list[dict[str, Any]],
    issue_period: date,
    data_through: date,
    dsha: str,
    seed: int,
    fitted_final: Any,
) -> None:
    # champion backtest rows (champion model only, labeled history)
    for p in bt["predictions"]:
        if p["adapter_id"] != champion_adapter:
            continue
        value_id = str(stable_uuid("shadow-value", f"{run_id}:{p['target_period']}:{p['horizon']}:BACKTEST"))
        conn.execute(
            """
            insert into epidemic_kg.kg_forecast_shadow_values
                (value_id, run_id, target_period_start, target_period_end, horizon,
                 yhat, lower_value, upper_value, actual_value, is_observed, is_imputed,
                 source_observation_ids, row_type, properties)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,false,%s,'BACKTEST',%s::jsonb)
            on conflict (run_id, target_period_start, horizon, row_type) do nothing
            """,
            (
                value_id,
                run_id,
                p["target_period"],
                p["target_period"],
                p["horizon"],
                p["yhat"],
                p["lower_bound"],
                p["upper_bound"],
                p["actual_value"],
                p["actual_value"] is not None,
                [p["actual_observation_id"]] if p["actual_observation_id"] else [],
                json.dumps(
                    {
                        "adapter_id": champion_adapter,
                        "model_artifact_sha256": p["model_artifact_sha256"],
                        "interval_level": p["interval_level"],
                        "interval_method": p["interval_method"],
                        "shadow": True,
                    },
                    default=str,
                ),
            ),
        )
    # future H1-H4 from issue period
    for p in future_preds:
        target = period_add(issue_period, ctx["frequency"], p["horizon"])
        value_id = str(stable_uuid("shadow-value", f"{run_id}:{target.isoformat()}:{p['horizon']}:FUTURE"))
        conn.execute(
            """
            insert into epidemic_kg.kg_forecast_shadow_values
                (value_id, run_id, target_period_start, target_period_end, horizon,
                 yhat, lower_value, upper_value, actual_value, is_observed, is_imputed,
                 source_observation_ids, row_type, properties)
            values (%s,%s,%s,%s,%s,%s,%s,%s,NULL,false,false,'{}','FUTURE',%s::jsonb)
            on conflict (run_id, target_period_start, horizon, row_type) do nothing
            """,
            (
                value_id,
                run_id,
                target,
                target,
                p["horizon"],
                p["yhat"],
                p["lower_bound"],
                p["upper_bound"],
                json.dumps(
                    {
                        "adapter_id": champion_adapter,
                        "issue_period": issue_period.isoformat(),
                        "data_through_period": data_through.isoformat(),
                        "feature_max_period": data_through.isoformat(),
                        "dataset_sha256": dsha,
                        "seed": seed,
                        "model_artifact_sha256": fitted_final.artifact_sha256,
                        "interval_level": p["interval_level"],
                        "interval_method": p.get("interval_method"),
                        "feedback_maturity": MATURITY_AWAITING,
                        "shadow": True,
                        "promotion_eligible": False,
                    },
                    default=str,
                ),
            ),
        )


def _persist_objective_feedback(
    conn: Any,
    run_id: str,
    bt: dict[str, Any],
    future_preds: list[dict[str, Any]],
    issue_period: date,
    ctx: dict[str, Any],
    rows: list[TrainingRow],
) -> dict[str, int]:
    matured = awaiting = 0
    # historical backtest feedback: real later observations vs predictions
    for p in bt["predictions"]:
        if p["actual_value"] is None or p["yhat"] is None:
            continue
        rsha = sha256_json(
            {
                "run_id": run_id,
                "adapter_id": p["adapter_id"],
                "target_period": p["target_period"],
                "horizon": p["horizon"],
                "actual_observation_id": p["actual_observation_id"],
                "actual_revision": p["actual_revision"],
                "kind": "BACKTEST",
            }
        )
        value_id = str(stable_uuid("shadow-value", f"{run_id}:{p['target_period']}:{p['horizon']}:BACKTEST"))
        hit = (
            p["lower_bound"] is not None
            and p["upper_bound"] is not None
            and p["lower_bound"] <= p["actual_value"] <= p["upper_bound"]
        )
        conn.execute(
            """
            insert into epidemic_kg.kg_forecast_objective_feedback
                (feedback_id, run_id, forecast_value_id, adapter_id, target_period,
                 actual_observation_id, actual_value, actual_revision, actual_available_at,
                 absolute_error, squared_error, interval_hit, direction_correct,
                 feedback_maturity, reconciliation_sha256)
            values (%s,%s,%s,%s,%s,%s,%s,%s,now(),%s,%s,%s,%s,%s,%s)
            on conflict (reconciliation_sha256) do nothing
            """,
            (
                str(stable_uuid("obj-feedback", rsha)),
                run_id,
                value_id,
                p["adapter_id"],
                p["target_period"],
                p["actual_observation_id"],
                p["actual_value"],
                p["actual_revision"],
                abs(p["yhat"] - p["actual_value"]),
                (p["yhat"] - p["actual_value"]) ** 2,
                hit,
                p["direction_correct"],
                MATURITY_MATURED,
                rsha,
            ),
        )
        matured += 1
    # future values: awaiting actuals
    for p in future_preds:
        target = period_add(issue_period, ctx["frequency"], p["horizon"])
        value_id = str(stable_uuid("shadow-value", f"{run_id}:{target.isoformat()}:{p['horizon']}:FUTURE"))
        rsha = sha256_json({"run_id": run_id, "value_id": value_id, "kind": "FUTURE", "status": MATURITY_AWAITING})
        conn.execute(
            """
            insert into epidemic_kg.kg_forecast_objective_feedback
                (feedback_id, run_id, forecast_value_id, adapter_id, target_period,
                 actual_observation_id, actual_value, actual_revision, actual_available_at,
                 absolute_error, squared_error, interval_hit, direction_correct,
                 feedback_maturity, reconciliation_sha256)
            values (%s,%s,%s,%s,%s,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,%s,%s)
            on conflict (reconciliation_sha256) do nothing
            """,
            (
                str(stable_uuid("obj-feedback", rsha)),
                run_id,
                value_id,
                ctx["adapter_id"],
                target,
                MATURITY_AWAITING,
                rsha,
            ),
        )
        awaiting += 1
    return {"matured": matured, "awaiting_actual": awaiting}


def _persist_learning_cycle(
    conn: Any, run_id: str, ctx: dict[str, Any], bt: dict[str, Any], champion: dict[str, Any], feedback: dict[str, int]
) -> dict[str, Any]:
    evidence = {
        "run_id": run_id,
        "recommendation_id": ctx["recommendation_id"],
        "recommended_adapter": ctx["adapter_id"],
        "preexecution_score": float(ctx["total_score"]),
        "overall_metrics": {a: m[0] for a, m in bt["metrics"].items()},
        "champion": champion,
        "feedback": feedback,
        "learning_rule": "CONTROLLED_BATCH_LEARNING",
    }
    esha = sha256_json(evidence)
    cycle_id = str(stable_uuid("learning-cycle", f"{run_id}:INITIAL_HISTORICAL_REPLAY"))
    conn.execute(
        """
        insert into epidemic_kg.kg_learning_cycles
            (learning_cycle_id, run_id, recommendation_id, cycle_type, status,
             min_matured_feedback, matured_feedback_count, evidence, evidence_sha256)
        values (%s,%s,%s,'INITIAL_HISTORICAL_REPLAY','COMPLETED',%s,%s,%s::jsonb,%s)
        on conflict (run_id, cycle_type) do nothing
        """,
        (cycle_id, run_id, ctx["recommendation_id"], 1, feedback["matured"], json.dumps(evidence, default=str), esha),
    )
    conn.execute(
        """
        insert into epidemic_kg.kg_champion_decisions
            (decision_id, learning_cycle_id, run_id, champion_adapter_id, champion_source,
             kg_model_status, reason, decided_by, promotion_applied)
        values (%s,%s,%s,%s,%s,%s,%s,'SYSTEM_BACKTEST',false)
        on conflict (learning_cycle_id) do nothing
        """,
        (
            str(stable_uuid("champion-decision", cycle_id)),
            cycle_id,
            run_id,
            champion["champion_adapter_id"],
            champion["champion_source"],
            champion["kg_model_status"],
            champion["champion_reason"],
        ),
    )
    return {
        "learning_cycle_id": cycle_id,
        "evidence_sha256": esha,
        "champion_adapter_id": champion["champion_adapter_id"],
        "champion_source": champion["champion_source"],
        "kg_model_status": champion["kg_model_status"],
    }


# -------------------------------------------------------------- reconciliation
def reconcile_feedback(conn: Any, run_id: str | None = None) -> dict[str, int]:
    """Controlled reconciliation entry: for AWAITING_ACTUAL objective feedback
    whose target period now has an official observation, record matured
    feedback. Immutable history: a revision change supersedes the old row and
    appends a new one; trusted observations are only read, never written."""
    conn = _as_tuple_conn(conn)
    clause = "and f.run_id = %s" if run_id else ""
    params: tuple[Any, ...] = (run_id,) if run_id else ()
    rows = conn.execute(
        f"""
        select f.feedback_id::text, f.run_id::text, f.forecast_value_id::text,
               f.adapter_id, f.target_period, f.actual_revision,
               v.yhat, v.lower_value, v.upper_value,
               r.cadence, r.country_alpha3, r.disease_slug, r.metric_code
        from epidemic_kg.kg_forecast_objective_feedback f
        join epidemic_kg.kg_forecast_shadow_values v on v.value_id = f.forecast_value_id
        join epidemic_kg.kg_forecast_shadow_runs r on r.run_id = f.run_id
        where f.feedback_maturity in (%s, %s) {clause}
        """,
        (MATURITY_AWAITING, MATURITY_MATURED) + params,
    ).fetchall()
    matured = superseded = still_awaiting = 0
    for row in rows:
        (
            feedback_id,
            frun_id,
            value_id,
            adapter_id,
            target_period,
            old_revision,
            yhat,
            lo,
            hi,
            cadence,
            country,
            disease,
            metric,
        ) = row
        actual = conn.execute(
            """
            select observation_id::text, value_numeric, revision, created_at
            from epidemic.dashboard_history_series_v2
            where country_alpha3=%s and disease_slug=%s and metric_code=%s
              and granularity=%s and period_start=%s and latest_rank=1
            """,
            (country, disease, metric, cadence, target_period),
        ).fetchone()
        if not actual:
            still_awaiting += 1
            continue
        obs_id, value, revision, available_at = actual
        if old_revision is not None and int(revision) <= int(old_revision):
            continue  # already matured at this revision; nothing new
        if old_revision is not None:
            conn.execute(
                "update epidemic_kg.kg_forecast_objective_feedback set feedback_maturity=%s where feedback_id=%s",
                (MATURITY_SUPERSEDED, feedback_id),
            )
            superseded += 1
        rsha = sha256_json(
            {
                "run_id": frun_id,
                "value_id": value_id,
                "actual_observation_id": obs_id,
                "actual_revision": int(revision),
                "kind": "RECONCILED",
            }
        )
        abs_e = abs(float(yhat) - float(value)) if yhat is not None else None
        sq_e = (float(yhat) - float(value)) ** 2 if yhat is not None else None
        hit = None
        if yhat is not None and lo is not None and hi is not None:
            hit = float(lo) <= float(value) <= float(hi)
        conn.execute(
            """
            insert into epidemic_kg.kg_forecast_objective_feedback
                (feedback_id, run_id, forecast_value_id, adapter_id, target_period,
                 actual_observation_id, actual_value, actual_revision, actual_available_at,
                 absolute_error, squared_error, interval_hit, direction_correct,
                 feedback_maturity, reconciliation_sha256)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s,%s)
            on conflict (reconciliation_sha256) do nothing
            """,
            (
                str(stable_uuid("obj-feedback", rsha)),
                frun_id,
                value_id,
                adapter_id,
                target_period,
                obs_id,
                float(value),
                int(revision),
                available_at,
                abs_e,
                sq_e,
                hit,
                MATURITY_MATURED,
                rsha,
            ),
        )
        matured += 1
    return {"newly_matured": matured, "superseded_by_revision": superseded, "still_awaiting": still_awaiting}


# --------------------------------------------------------- eligibility router
def eligibility_verdict(stats: dict[str, Any]) -> dict[str, Any]:
    """Universal initial-triage router for any new series. A series always gets
    a verdict; a verdict never guarantees a numeric forecast."""
    n = int(stats.get("n_valid", 0))
    coverage = float(stats.get("coverage", 0.0) or 0.0)
    frequency = stats.get("frequency")
    trusted = bool(stats.get("trusted", True))
    min_obs = int(stats.get("adapter_min_obs", 12))
    if not trusted:
        code = "QUARANTINED_NO_FORECAST"
    elif n == 0:
        code = "NO_VALID_OBSERVATION"
    elif n <= 3:
        code = "SCENARIO_RISK_ONLY"
    elif n <= 23:
        code = "PRELIMINARY_BASELINE"
    elif frequency == "week" and n >= 104 and coverage >= 0.95:
        code = "SEASONAL_FORMAL_CANDIDATE"
    elif frequency == "week" and n >= 52 and coverage >= 0.95:
        code = "DEMO_FORECAST"
    elif frequency == "month" and n >= 24 and coverage >= 0.95:
        code = "SEASONAL_FORMAL_CANDIDATE"
    elif n >= min_obs:
        code = "KG_MODEL_SHADOW"
    else:
        code = "PRELIMINARY_BASELINE"
    return {
        "eligibility": code,
        "n_valid": n,
        "coverage": coverage,
        "frequency": frequency,
        "adapter_min_obs": min_obs,
        "router_version": LOOP_VERSION,
    }
