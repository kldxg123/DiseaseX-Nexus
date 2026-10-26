"""Database orchestration for the parameter-tuning + controlled self-learning demo.

Flow of ``execute_pt_demo`` (all Shadow, nothing trusted is ever written):

    snapshot freeze -> split + contracts -> bounded tuning (cycle 1)
    -> shadow run row -> validation backtest into the 0040 ledger tables
    -> test-window predictions stored with actual=NULL + AWAITING feedback
    -> exactly-once test evaluation against the FROZEN snapshot rows
    -> test feedback matured as DEMO_HISTORICAL_REPLAY rows
    -> learning cycle PARAMETER_TUNING_EVALUATION + champion decision
    -> future H1-H4 from the champion (AWAITING_ACTUAL)
    -> cycle 2 controlled retrain (historical replay trigger) + decision.

Idempotency: every insert uses bare ON CONFLICT DO NOTHING with stable_uuid
ids and content hashes, so partial reruns and concurrent callers converge;
a completed pair of tuning runs short-circuits to IDEMPOTENT_REPLAY both for
the same idempotency key AND for a different key with identical content
(snapshot/split/adapter/cycle), which never writes new rows.

This module is importable without a database or psycopg: all DB access happens
inside functions, and row access supports both psycopg dict_row and tuple rows.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from epidemic_core.forecast_model_kg.dual_loop import dataset_sha256, period_add
from epidemic_core.kg_pt import (
    CHAMPION_MIN_IMPROVEMENT,
    DEMO_ADAPTER_ID,
    DEMO_FREQUENCY,
    DEMO_KEY,
    DEMO_RECOMMENDATION_ID,
    HORIZONS,
    MAX_SECONDS,
    MAX_TRIALS,
    MISSING_SEMANTICS,
    NO_REMAINING_HELDOUT,
    PT_IMPL_VERSION,
    PT_SEED,
    REPLAY_KIND,
    RETRAIN_MIN_MATURED,
    TEST_N,
    TRAIN_N,
    VAL_N,
)
from epidemic_core.kg_pt.contracts import contracts_for, default_params
from epidemic_core.kg_pt.metrics import agreement_pct, cell_metrics, mase_denominator
from epidemic_core.kg_pt.split import (
    TemporalSplit,
    make_split,
    test_origin_indices,
    tuning_origin_indices,
)
from epidemic_core.kg_pt.tuner import TuningResult, run_tuning
from epidemic_core.kg_r3c.execution import (
    BASELINE_IDS,
    TrainingRow,
    baseline_not_eligible,
    fit_model,
    nb_deviance,
    predict_model,
    sha256_json,
    stable_uuid,
)

SCHEMA = "epidemic_kg"
SOURCE_TABLE = "epidemic.dashboard_history_series_v2"

MATURITY_AWAITING = "AWAITING_ACTUAL"
MATURITY_MATURED = "MATURED"
MATURITY_SUPERSEDED = "SUPERSEDED_REVISION"

ROW_TYPE_TEST_REPLAY = "TEST_REPLAY"
ROW_TYPE_FUTURE = "FUTURE"

CYCLE_TYPE_EVALUATION = "PARAMETER_TUNING_EVALUATION"
CYCLE_TYPE_RETRAIN = "CONTROLLED_RETRAIN_HISTORICAL_REPLAY"

LIMITATIONS = [
    "SHADOW_ONLY_NO_PROMOTION",
    "DEMO_HISTORICAL_REPLAY_NOT_A_LIVE_EVENT",
    "AGREEMENT_PCT_IS_WAPE_DERIVED_READABILITY_METRIC_NOT_ACCURACY",
    "NO_REMAINING_HELDOUT_AFTER_CYCLE2",
    "DECISION_SUPPORT_NOT_MEDICAL_ADVICE",
]


# ---------------------------------------------------------------- row access
def _val(row: Any, key: str, idx: int) -> Any:
    """Read a column from either a psycopg dict_row or a tuple row."""
    if row is None:
        return None
    if isinstance(row, Mapping):
        return row[key]
    return row[idx]


# ------------------------------------------------------------------ snapshot
def snapshot_rows_payload(rows: Sequence[TrainingRow]) -> list[dict[str, Any]]:
    """Pure frozen-row payload for one snapshot (hash input + DB rows)."""
    return [
        {
            "row_index": i,
            "period_start": r.period_start.isoformat(),
            "period_end": r.period_end.isoformat() if r.period_end else None,
            "value_numeric": r.value,
            "observation_id": r.observation_id,
            "revision": r.revision,
        }
        for i, r in enumerate(rows)
    ]


def compute_snapshot_sha256(rows: Sequence[TrainingRow]) -> str:
    """Pure content hash of the frozen rows; identical input -> identical sha."""
    return sha256_json(snapshot_rows_payload(rows))


def _load_source_rows(conn: Any, country: str, disease: str, indicator: str, frequency: str) -> list[TrainingRow]:
    rows = conn.execute(
        """
        select observation_id::text, period_start, period_end, value_numeric, revision
        from epidemic.dashboard_history_series_v2
        where country_alpha3 = %s and disease_slug = %s and metric_code = %s
          and granularity = %s and latest_rank = 1
        order by period_start
        """,
        (country, disease, indicator, frequency),
    ).fetchall()
    return [
        TrainingRow(
            period_start=_val(r, "period_start", 1),
            period_end=_val(r, "period_end", 2),
            value=None if _val(r, "value_numeric", 3) is None else float(_val(r, "value_numeric", 3)),
            observation_id=_val(r, "observation_id", 0),
            revision=int(_val(r, "revision", 4) or 1),
        )
        for r in rows
    ]


def _missing_periods(rows: Sequence[TrainingRow], frequency: str) -> list[str]:
    """Grid periods between first and last that have no row (never imputed)."""
    present = {r.period_start for r in rows}
    missing: list[str] = []
    cur = rows[0].period_start
    last = rows[-1].period_start
    while cur < last:
        cur = period_add(cur, frequency, 1)
        if cur < last and cur not in present:
            missing.append(cur.isoformat())
    return missing


def ensure_demo_snapshot(
    conn: Any,
    demo_key: str = DEMO_KEY,
    rows_override: Sequence[TrainingRow] | None = None,
    source_table_override: str | None = None,
) -> dict[str, Any]:
    """Freeze the latest source rows into kg_pt_demo_snapshots(+_rows).

    Content-addressed: an identical row set reuses the existing snapshot.
    Never writes trusted tables; snapshot rows are insert-only by grant.
    ``rows_override`` lets the kg_qwen layer freeze a series from a different
    trusted source table (e.g. normalized_observations for the governance
    blind window) through the exact same immutability machinery; the real
    source table is recorded via ``source_table_override``.
    """
    country, disease, indicator, frequency = demo_key.split(":")
    rows = (
        list(rows_override)
        if rows_override is not None
        else _load_source_rows(conn, country, disease, indicator, frequency)
    )
    if not rows:
        raise ValueError(f"PT_DEMO_NO_SOURCE_ROWS:{demo_key}")
    ssha = compute_snapshot_sha256(rows)
    snapshot_id = str(stable_uuid("pt-snapshot", ssha))
    existing = conn.execute(
        f"select snapshot_id from {SCHEMA}.kg_pt_demo_snapshots where snapshot_sha256 = %s", (ssha,)
    ).fetchone()
    dsha = dataset_sha256(rows, country, disease, indicator, frequency)
    if not existing:
        payload = snapshot_rows_payload(rows)
        conn.execute(
            f"""
            insert into {SCHEMA}.kg_pt_demo_snapshots
                (snapshot_id, demo_key, country, disease, indicator, frequency, source_table,
                 n_obs, first_period, last_period, data_through, revision_max, observation_keys,
                 missing_periods, missing_semantics, dataset_sha256, snapshot_sha256)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s)
            on conflict do nothing
            """,
            (
                snapshot_id,
                demo_key,
                country,
                disease,
                indicator,
                frequency,
                source_table_override or SOURCE_TABLE,
                len(rows),
                rows[0].period_start,
                rows[-1].period_start,
                rows[-1].period_start,
                max(r.revision for r in rows),
                json.dumps([r.observation_id for r in rows], default=str),
                json.dumps(_missing_periods(rows, frequency)),
                MISSING_SEMANTICS,
                dsha,
                ssha,
            ),
        )
        for p in payload:
            conn.execute(
                f"""
                insert into {SCHEMA}.kg_pt_demo_snapshot_rows
                    (snapshot_id, row_index, period_start, period_end, value_numeric,
                     observation_id, revision, row_sha256)
                values (%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict do nothing
                """,
                (
                    snapshot_id,
                    p["row_index"],
                    p["period_start"],
                    p["period_end"],
                    p["value_numeric"],
                    p["observation_id"],
                    p["revision"],
                    sha256_json(p),
                ),
            )
    return {
        "snapshot_id": snapshot_id,
        "snapshot_sha256": ssha,
        "dataset_sha256": dsha,
        "rows": rows,
        "country": country,
        "disease": disease,
        "indicator": indicator,
        "frequency": frequency,
        "reused": bool(existing),
    }


def _load_frozen_rows(conn: Any, snapshot_id: str) -> list[TrainingRow]:
    """Read back the FROZEN snapshot rows (never the live view) for labels."""
    rows = conn.execute(
        f"""
        select observation_id, period_start, period_end, value_numeric, revision
        from {SCHEMA}.kg_pt_demo_snapshot_rows
        where snapshot_id = %s
        order by row_index
        """,
        (snapshot_id,),
    ).fetchall()
    return [
        TrainingRow(
            period_start=_val(r, "period_start", 1),
            period_end=_val(r, "period_end", 2),
            value=None if _val(r, "value_numeric", 3) is None else float(_val(r, "value_numeric", 3)),
            observation_id=_val(r, "observation_id", 0),
            revision=int(_val(r, "revision", 4) or 1),
        )
        for r in rows
    ]


# --------------------------------------------------------------------- split
def ensure_split(
    conn: Any,
    snapshot_sha256: str,
    periods: Sequence[date],
    frequency: str = DEMO_FREQUENCY,
    train_n: int = TRAIN_N,
    val_n: int = VAL_N,
    test_n: int = TEST_N,
) -> TemporalSplit:
    """Content-addressed split upsert. For an empty test segment (demo end)
    test_start/test_end are stored as the first period after validation_end
    with test_rows=0; the NO_REMAINING_HELDOUT semantics are recorded in the
    learning-cycle evidence."""
    split = make_split(periods, train_n, val_n, test_n)
    split_id = str(stable_uuid("pt-split", split.split_sha256))
    test_start = split.test_start or period_add(split.validation_end, frequency, 1)
    test_end = split.test_end or test_start
    conn.execute(
        f"""
        insert into {SCHEMA}.kg_pt_temporal_splits
            (split_id, snapshot_sha256, train_start, train_end, validation_start, validation_end,
             test_start, test_end, train_rows, validation_rows, test_rows, split_sha256)
        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        on conflict do nothing
        """,
        (
            split_id,
            snapshot_sha256,
            split.train_start,
            split.train_end,
            split.validation_start,
            split.validation_end,
            test_start,
            test_end,
            split.train_rows,
            split.validation_rows,
            split.test_rows,
            split.split_sha256,
        ),
    )
    return split


# ----------------------------------------------------------------- contracts
def ensure_contracts(conn: Any, adapter_id: str, frequency: str = DEMO_FREQUENCY) -> list[str]:
    """Content-addressed contract upserts; returns the contract shas."""
    shas: list[str] = []
    for c in contracts_for(adapter_id, frequency):
        payload = {
            "adapter_id": c.adapter_id,
            "parameter_name": c.parameter_name,
            "data_type": c.data_type,
            "default_value": c.default_value,
            "lower_bound": c.lower_bound,
            "upper_bound": c.upper_bound,
            "discrete_candidates": c.discrete_candidates,
            "transform": c.transform,
            "parameter_source": c.parameter_source,
            "evidence_locator": c.evidence_locator,
            "code_source": c.code_source,
            "applicable_profile": c.applicable_profile,
            "missing_reason": c.missing_reason,
            "safety_constraint": c.safety_constraint,
            "tunable": c.tunable,
        }
        csha = sha256_json(payload)
        shas.append(csha)
        conn.execute(
            f"""
            insert into {SCHEMA}.kg_pt_parameter_contracts
                (contract_id, adapter_id, parameter_name, data_type, default_value, lower_bound,
                 upper_bound, discrete_candidates, transform, parameter_source, evidence_locator,
                 code_source, applicable_profile, missing_reason, safety_constraint, tunable,
                 contract_sha256)
            values (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s)
            on conflict do nothing
            """,
            (
                str(stable_uuid("pt-contract", csha)),
                c.adapter_id,
                c.parameter_name,
                c.data_type,
                c.default_value,
                c.lower_bound,
                c.upper_bound,
                json.dumps(c.discrete_candidates, default=str),
                c.transform,
                c.parameter_source,
                c.evidence_locator,
                c.code_source,
                json.dumps(c.applicable_profile, default=str),
                c.missing_reason,
                c.safety_constraint,
                c.tunable,
                csha,
            ),
        )
    return shas


# ------------------------------------------------------------------ backtest
def _run_window_backtest(
    rows: Sequence[TrainingRow],
    models: dict[str, dict[str, Any]],
    origin_indices: list[int],
    frequency: str,
    seed: int,
    with_labels: bool,
) -> dict[str, Any]:
    """Fit/predict every model on identical origins.

    MASE denominators are PER-ORIGIN and computed from that origin's training
    slice only (never the full series). With with_labels=False no label is
    read: actuals stay None (predictions themselves never depend on labels).
    """
    folds: list[dict[str, Any]] = []
    predictions: list[dict[str, Any]] = []
    per_model: dict[str, list[dict[str, Any]]] = {m: [] for m in models}
    artifacts: dict[str, dict[int, Any]] = {m: {} for m in models}
    not_eligible: dict[str, str] = {}
    for origin_idx in origin_indices:
        origin_row = rows[origin_idx]
        train_rows = list(rows[: origin_idx + 1])  # feature_max_period <= origin, hard boundary
        train_values = [r.value for r in train_rows if r.value is not None]
        try:
            denom = mase_denominator([r.value for r in train_rows])
        except ValueError:
            denom = None
        for adapter_id, spec in models.items():
            fit_as = spec.get("fit_as", adapter_id)  # label may differ from adapter (champion comparisons)
            fitted = fit_model(
                fit_as, train_rows, spec.get("parameters") or {}, seed, origin_row.period_start, frequency
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
                actual_row = None
                if with_labels and target_idx < len(rows):
                    candidate = rows[target_idx]
                    if candidate.period_start == target_period:  # gap -> label stays missing
                        actual_row = candidate
                actual = actual_row.value if actual_row else None
                deviance = None
                if actual is not None and p["yhat"] is not None:
                    shape = fitted.state.get("nb_shape") if fit_as == "negbin_dglm.v1" else None
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
                    "mase_denominator": denom,
                    "not_eligible": p.get("not_eligible"),
                }
                predictions.append(row)
                per_model[adapter_id].append(row)
    return {
        "folds": folds,
        "predictions": predictions,
        "per_model": per_model,
        "artifacts": artifacts,
        "not_eligible": not_eligible,
    }


def _window_metrics(preds: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """dual_loop.compute_metrics shape, but MASE uses the per-origin train
    slice denominator attached to each prediction (never a full-series one)."""
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
        base = cell_metrics([(p["yhat"], p["actual_value"], p["lower_bound"], p["upper_bound"]) for p in rows])
        mase_terms = [
            abs(p["yhat"] - p["actual_value"]) / p["mase_denominator"] for p in labeled if p["mase_denominator"]
        ]
        devs = [p["deviance"] for p in labeled if p.get("deviance") is not None]
        out[horizon] = {
            "horizon": horizon,
            "mae": round(base["mae"], 6),
            "rmse": round(base["rmse"], 6),
            "wape": round(base["wape"], 6) if base["wape"] is not None else None,
            "mase": round(sum(mase_terms) / len(mase_terms), 6) if mase_terms else None,
            "deviance": round(sum(devs) / len(devs), 6) if devs else None,
            "interval_coverage": round(base["coverage"], 6) if base["coverage"] is not None else None,
            "interval_avg_width": round(base["avg_width"], 6) if base["avg_width"] is not None else None,
            "forecast_row_count": len(rows),
            "labeled_row_count": n,
            "missing_label_count": missing,
        }
    return out


def _persist_backtest_ledger(conn: Any, run_id: str, bt: dict[str, Any]) -> None:
    """Validation backtest -> 0040 folds/predictions/metrics ledger tables."""
    fold_ids: dict[tuple[str, int], str] = {}
    for f in bt["folds"]:
        fold_id = str(stable_uuid("fold", f"{run_id}:{f['adapter_id']}:{f['origin_period']}"))
        fold_ids[(f["adapter_id"], f["origin_index"])] = fold_id
        conn.execute(
            f"""
            insert into {SCHEMA}.kg_shadow_backtest_folds
                (fold_id, run_id, adapter_id, origin_index, origin_period, train_row_count,
                 train_sha256, model_artifact_sha256, status)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict do nothing
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
            f"""
            insert into {SCHEMA}.kg_shadow_backtest_predictions
                (prediction_id, fold_id, run_id, adapter_id, origin_index, origin_period,
                 target_period, horizon, yhat, lower_bound, upper_bound, interval_level,
                 interval_method, actual_value, actual_observation_id, actual_revision,
                 abs_error, squared_error, deviance, direction_correct,
                 model_artifact_sha256, not_eligible, prediction_sha256)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict do nothing
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
                f"""
                insert into {SCHEMA}.kg_shadow_model_metrics
                    (metric_id, run_id, adapter_id, horizon, mae, rmse, wape, mase,
                     deviance, interval_coverage, interval_avg_width, forecast_row_count,
                     labeled_row_count, missing_label_count, metrics_sha256)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict do nothing
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


# ------------------------------------------------------------- tuning persist
def _find_tuning_run_by_content(
    conn: Any, snapshot_sha256: str, split_sha256: str, adapter_id: str, cycle_index: int, completed_only: bool = False
) -> dict[str, Any] | None:
    """Content-addressed lookup of an existing tuning run. Identical demo
    content under a different idempotency key must converge on the already
    persisted run (no duplicate run for the same snapshot/split/adapter/cycle)."""
    clause = "and status = 'COMPLETED'" if completed_only else ""
    row = conn.execute(
        f"select tuning_run_id::text, shadow_run_id::text, split_sha256, best_trial_id::text, status "
        f"from {SCHEMA}.kg_pt_tuning_runs where snapshot_sha256 = %s and split_sha256 = %s "
        f"and adapter_id = %s and cycle_index = %s {clause} "
        f"order by case when status = 'COMPLETED' then 0 else 1 end, created_at asc limit 1",
        (snapshot_sha256, split_sha256, adapter_id, cycle_index),
    ).fetchone()
    if not row:
        return None
    return {
        "tuning_run_id": _val(row, "tuning_run_id", 0),
        "shadow_run_id": _val(row, "shadow_run_id", 1),
        "split_sha256": _val(row, "split_sha256", 2),
        "best_trial_id": _val(row, "best_trial_id", 3),
        "status": _val(row, "status", 4),
    }


def _persist_tuning_run(
    conn: Any,
    tuning_run_id: str,
    shadow_run_id: str,
    snapshot_sha256: str,
    split_sha256: str,
    adapter_id: str,
    cycle_index: int,
    idempotency_key: str,
    result: TuningResult,
) -> tuple[str, str | None]:
    """Insert the tuning run (RUNNING) plus every trial and origin row.

    Content dedup: if a run with identical (snapshot_sha256, split_sha256,
    adapter_id, cycle_index) already exists — including one left RUNNING by a
    crashed attempt — its id is reused and all child inserts converge via
    ON CONFLICT DO NOTHING, so a second idempotency key never duplicates a
    content-identical run. Returns (authoritative tuning_run_id, best_trial_id)."""
    existing = _find_tuning_run_by_content(conn, snapshot_sha256, split_sha256, adapter_id, cycle_index)
    if existing is not None:
        tuning_run_id = str(existing["tuning_run_id"])
    else:
        conn.execute(
            f"""
            insert into {SCHEMA}.kg_pt_tuning_runs
                (tuning_run_id, shadow_run_id, recommendation_id, snapshot_sha256, split_sha256,
                 adapter_id, cycle_index, seed, max_trials, max_seconds, objective, code_version,
                 status, idempotency_key, best_trial_id, started_at)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'RUNNING',%s,NULL,now())
            on conflict do nothing
            """,
            (
                tuning_run_id,
                shadow_run_id,
                DEMO_RECOMMENDATION_ID,
                snapshot_sha256,
                split_sha256,
                adapter_id,
                cycle_index,
                result.seed,
                result.max_trials,
                result.max_seconds,
                result.objective,
                result.code_version,
                idempotency_key,
            ),
        )
    trial_ids: dict[int, str] = {}
    for t in result.trials:
        trial_id = str(stable_uuid("pt-trial", t.trial_sha256))
        trial_ids[t.trial_index] = trial_id
        conn.execute(
            f"""
            insert into {SCHEMA}.kg_pt_tuning_trials
                (trial_id, tuning_run_id, trial_index, params, params_sha256, parameter_source,
                 n_origins, n_labeled, common_wape, per_horizon_metrics, coverage,
                 avg_interval_width, complexity_penalty, objective_value, gates, gate_pass,
                 failure_reason, elapsed_ms, artifact_sha256, code_version,
                 dataset_snapshot_sha256, trial_sha256)
            values (%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s)
            on conflict do nothing
            """,
            (
                trial_id,
                tuning_run_id,
                t.trial_index,
                json.dumps(t.params, default=str),
                t.params_sha256,
                json.dumps(t.parameter_source, default=str),
                len(t.origins),
                sum(o.n_labeled for o in t.origins),
                t.common_wape,
                json.dumps(t.per_horizon_metrics, default=str),
                t.coverage,
                t.avg_interval_width,
                t.complexity_penalty,
                t.objective_value,
                json.dumps(t.gates, default=str),
                t.gate_pass,
                t.failure_reason,
                t.elapsed_ms,
                t.artifact_sha256,
                result.code_version,
                t.dataset_snapshot_sha256,
                t.trial_sha256,
            ),
        )
        for o in t.origins:
            conn.execute(
                f"""
                insert into {SCHEMA}.kg_pt_trial_origins
                    (origin_id, trial_id, origin_index, origin_period, issue_period, data_through,
                     feature_max_period, label_periods, train_row_count, validation_row_count,
                     dataset_sha256, seed, artifact_sha256, origin_sha256)
                values (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s)
                on conflict do nothing
                """,
                (
                    str(stable_uuid("pt-origin", f"{t.trial_sha256}:{o.origin_index}")),
                    trial_id,
                    o.origin_index,
                    o.origin_period,
                    o.issue_period,
                    o.data_through,
                    o.feature_max_period,
                    json.dumps([p.isoformat() for p in o.label_periods]),
                    o.train_row_count,
                    o.validation_row_count,
                    o.dataset_sha256,
                    o.seed,
                    o.artifact_sha256,
                    o.origin_sha256,
                ),
            )
    best_trial_id = trial_ids.get(result.best_trial_index) if result.best_trial_index is not None else None
    conn.execute(
        f"update {SCHEMA}.kg_pt_tuning_runs set status=%s, completed_at=now(), best_trial_id=%s where tuning_run_id=%s",
        (result.status, best_trial_id, tuning_run_id),
    )
    return tuning_run_id, best_trial_id


# ------------------------------------------------------ test values/feedback
def _value_id(run_id: str, target: date, horizon: int, row_type: str) -> str:
    return str(stable_uuid("shadow-value", f"{run_id}:{target.isoformat()}:{horizon}:{row_type}"))


def _persist_test_shadow_values(conn: Any, run_id: str, cells: list[dict[str, Any]], snapshot_sha256: str) -> list[str]:
    """Tuned-model test-window predictions with actual_value=NULL (labels are
    joined only afterwards, from the frozen snapshot)."""
    value_ids: list[str] = []
    for c in cells:
        vid = _value_id(run_id, date.fromisoformat(c["target_period"]), c["horizon"], ROW_TYPE_TEST_REPLAY)
        value_ids.append(vid)
        conn.execute(
            f"""
            insert into {SCHEMA}.kg_forecast_shadow_values
                (value_id, run_id, target_period_start, target_period_end, horizon,
                 yhat, lower_value, upper_value, actual_value, is_observed, is_imputed,
                 source_observation_ids, row_type, properties)
            values (%s,%s,%s,%s,%s,%s,%s,%s,NULL,false,false,'{{}}',%s,%s::jsonb)
            on conflict do nothing
            """,
            (
                vid,
                run_id,
                c["target_period"],
                c["target_period"],
                c["horizon"],
                c["yhat"],
                c["lower_bound"],
                c["upper_bound"],
                ROW_TYPE_TEST_REPLAY,
                json.dumps(
                    {
                        "adapter_id": c["adapter_id"],
                        "origin_period": c["origin_period"],
                        "interval_level": c["interval_level"],
                        "interval_method": c["interval_method"],
                        "model_artifact_sha256": c["model_artifact_sha256"],
                        "snapshot_sha256": snapshot_sha256,
                        "feedback_maturity": MATURITY_AWAITING,
                        "replay": REPLAY_KIND,
                        "shadow": True,
                        "promotion_eligible": False,
                    },
                    default=str,
                ),
            ),
        )
        rsha = sha256_json(
            {"run_id": run_id, "value_id": vid, "kind": ROW_TYPE_TEST_REPLAY, "status": MATURITY_AWAITING}
        )
        conn.execute(
            f"""
            insert into {SCHEMA}.kg_forecast_objective_feedback
                (feedback_id, run_id, forecast_value_id, adapter_id, target_period,
                 actual_observation_id, actual_value, actual_revision, actual_available_at,
                 absolute_error, squared_error, interval_hit, direction_correct,
                 feedback_maturity, reconciliation_sha256)
            values (%s,%s,%s,%s,%s,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,%s,%s)
            on conflict do nothing
            """,
            (
                str(stable_uuid("obj-feedback", rsha)),
                run_id,
                vid,
                c["adapter_id"],
                c["target_period"],
                MATURITY_AWAITING,
                rsha,
            ),
        )
    return value_ids


def _mature_test_feedback(
    conn: Any, run_id: str, adapter_id: str, cells: list[dict[str, Any]], frozen: Sequence[TrainingRow]
) -> dict[str, int]:
    """Exactly-once maturation of the test feedback rows: the AWAITING rows are
    superseded and new MATURED rows are appended whose reconciliation hash
    input carries kind='DEMO_HISTORICAL_REPLAY'. Labels come from the frozen
    snapshot rows, never from the live view."""
    labels = {r.period_start: r for r in frozen}
    matured = superseded = 0
    awaiting = conn.execute(
        f"""
        select feedback_id::text, forecast_value_id::text, target_period
        from {SCHEMA}.kg_forecast_objective_feedback
        where run_id = %s and feedback_maturity = %s
        """,
        (run_id, MATURITY_AWAITING),
    ).fetchall()
    pred_by_value = {
        _value_id(run_id, date.fromisoformat(c["target_period"]), c["horizon"], ROW_TYPE_TEST_REPLAY): c for c in cells
    }
    for row in awaiting:
        feedback_id = _val(row, "feedback_id", 0)
        value_id = _val(row, "forecast_value_id", 1)
        target_period = _val(row, "target_period", 2)
        c = pred_by_value.get(value_id)
        label = labels.get(target_period)
        if c is None or label is None or label.value is None or c["yhat"] is None:
            continue
        rsha = sha256_json(
            {
                "run_id": run_id,
                "value_id": value_id,
                "actual_observation_id": label.observation_id,
                "actual_revision": label.revision,
                "kind": REPLAY_KIND,
            }
        )
        hit = (
            c["lower_bound"] is not None
            and c["upper_bound"] is not None
            and float(c["lower_bound"]) <= float(label.value) <= float(c["upper_bound"])
        )
        conn.execute(
            f"""
            insert into {SCHEMA}.kg_forecast_objective_feedback
                (feedback_id, run_id, forecast_value_id, adapter_id, target_period,
                 actual_observation_id, actual_value, actual_revision, actual_available_at,
                 absolute_error, squared_error, interval_hit, direction_correct,
                 feedback_maturity, reconciliation_sha256)
            values (%s,%s,%s,%s,%s,%s,%s,%s,now(),%s,%s,%s,NULL,%s,%s)
            on conflict do nothing
            """,
            (
                str(stable_uuid("obj-feedback", rsha)),
                run_id,
                value_id,
                adapter_id,
                target_period,
                label.observation_id,
                float(label.value),
                label.revision,
                abs(float(c["yhat"]) - float(label.value)),
                (float(c["yhat"]) - float(label.value)) ** 2,
                hit,
                MATURITY_MATURED,
                rsha,
            ),
        )
        conn.execute(
            f"update {SCHEMA}.kg_forecast_objective_feedback set feedback_maturity=%s where feedback_id=%s",
            (MATURITY_SUPERSEDED, feedback_id),
        )
        matured += 1
        superseded += 1
    return {"test_matured": matured, "test_superseded": superseded}


# ------------------------------------------------------------ test evaluation
def _evaluate_test_window(
    conn: Any,
    tuning_run_id: str,
    split_sha256: str,
    per_model: dict[str, list[dict[str, Any]]],
    frozen: Sequence[TrainingRow],
    tuned_adapter: str,
    tuned_params: dict[str, Any],
) -> tuple[dict[str, str], dict[str, float | None], list[Any]]:
    """Exactly-once independent test evaluation for the tuned model plus the
    four baselines. Labels are joined from the FROZEN snapshot rows. Overall
    metric columns are computed on the all-model common labeled cells so the
    comparison is apples-to-apples; per-horizon metrics use each model's own
    labeled cells."""
    labels = {r.period_start: r for r in frozen}
    for cells in per_model.values():
        for c in cells:
            label = labels.get(date.fromisoformat(c["target_period"]))
            c["actual_value"] = label.value if label else None
            c["actual_observation_id"] = label.observation_id if label else None
            c["actual_revision"] = label.revision if label else None
    # all-model common labeled cells
    common: set[Any] | None = None
    for cells in per_model.values():
        keys = {
            (c["origin_index"], c["horizon"]) for c in cells if c["yhat"] is not None and c["actual_value"] is not None
        }
        common = keys if common is None else (common & keys)
    common = common or set()
    eval_ids: dict[str, str] = {}
    common_wapes: dict[str, float | None] = {}
    for adapter_id, cells in per_model.items():
        per_horizon: dict[str, Any] = {}
        for horizon in [0] + HORIZONS:
            hcells = [c for c in cells if horizon == 0 or c["horizon"] == horizon]
            m = cell_metrics([(c["yhat"], c["actual_value"], c["lower_bound"], c["upper_bound"]) for c in hcells])
            terms = [
                abs(c["yhat"] - c["actual_value"]) / c["mase_denominator"]
                for c in hcells
                if c["yhat"] is not None and c["actual_value"] is not None and c["mase_denominator"]
            ]
            m["mase"] = sum(terms) / len(terms) if terms else None
            per_horizon[str(horizon)] = m
        common_cells = [c for c in cells if (c["origin_index"], c["horizon"]) in common]
        overall = cell_metrics(
            [(c["yhat"], c["actual_value"], c["lower_bound"], c["upper_bound"]) for c in common_cells]
        )
        terms = [
            abs(c["yhat"] - c["actual_value"]) / c["mase_denominator"] for c in common_cells if c["mase_denominator"]
        ]
        mase = sum(terms) / len(terms) if terms else None
        wape = overall["wape"]
        common_wapes[adapter_id] = wape
        is_tuned = adapter_id == tuned_adapter
        params = tuned_params if is_tuned else (per_model_params(adapter_id))
        eval_payload = {
            "tuning_run_id": tuning_run_id,
            "adapter_id": adapter_id,
            "is_tuned_model": is_tuned,
            "params": params,
            "split_sha256": split_sha256,
            "common_cells": sorted([list(k) for k in common]),
            "overall": {**overall, "mase": mase},
        }
        esha = sha256_json(eval_payload)
        eval_id = str(stable_uuid("pt-test-eval", esha))
        eval_ids[adapter_id] = eval_id
        conn.execute(
            f"""
            insert into {SCHEMA}.kg_pt_test_evaluations
                (eval_id, tuning_run_id, adapter_id, is_tuned_model, params, params_sha256,
                 split_sha256, n_origins, per_horizon_metrics, mae, rmse, wape, smape, mase,
                 coverage, avg_interval_width, agreement_pct, eval_sha256)
            values (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict do nothing
            """,
            (
                eval_id,
                tuning_run_id,
                adapter_id,
                is_tuned,
                json.dumps(params, default=str),
                sha256_json(params),
                split_sha256,
                len({c["origin_index"] for c in cells}),
                json.dumps(per_horizon, default=str),
                overall["mae"],
                overall["rmse"],
                wape,
                overall["smape"],
                mase,
                overall["coverage"],
                overall["avg_width"],
                agreement_pct(wape),
                esha,
            ),
        )
    return eval_ids, common_wapes, sorted(common)


def per_model_params(adapter_id: str) -> dict[str, Any]:
    """Evaluation parameter label for baselines (their effective defaults)."""
    if adapter_id == "rolling_mean":
        return {"window": 4}
    if adapter_id == "seasonal_naive":
        return {"season_lag": 12}
    return {}


# ------------------------------------------------------------------ champion
def _decide_champion(
    tuned_wape: float | None, baseline_wapes: dict[str, float | None], tuned_gate_pass: bool
) -> dict[str, Any]:
    """Champion = tuned KG model only when its test common WAPE improves on the
    best baseline by at least CHAMPION_MIN_IMPROVEMENT and its tuning gates
    passed; otherwise the best baseline takes over (Shadow decision only)."""
    ranked: list[dict[str, Any]] = [
        {"adapter_id": a, "common_wape": w} for a, w in sorted(baseline_wapes.items()) if w is not None
    ]
    best_baseline = min(ranked, key=lambda r: r["common_wape"]) if ranked else None
    comparison = ranked + [{"adapter_id": "tuned", "common_wape": tuned_wape, "tuned": True}]
    if tuned_wape is not None and tuned_gate_pass:
        if best_baseline is None:
            return {
                "champion_adapter_id": "tuned",
                "champion_source": "KG_RECOMMENDED_MODEL",
                "kg_model_status": "EXECUTED_NO_COMMON_COMPARISON",
                "champion_reason": "NO_BASELINE_COMMON_SAMPLE",
                "tuned_common_wape": tuned_wape,
                "best_baseline": None,
                "improvement": None,
                "comparison": comparison,
            }
        improvement = (best_baseline["common_wape"] - tuned_wape) / best_baseline["common_wape"]
        if improvement >= CHAMPION_MIN_IMPROVEMENT:
            return {
                "champion_adapter_id": "tuned",
                "champion_source": "KG_RECOMMENDED_MODEL",
                "kg_model_status": "EXECUTED_CHAMPION",
                "champion_reason": "TUNED_KG_MODEL_COMMON_WAPE_IMPROVEMENT_GE_1PCT",
                "tuned_common_wape": tuned_wape,
                "best_baseline": best_baseline,
                "improvement": round(improvement, 6),
                "comparison": comparison,
            }
    if best_baseline is not None:
        improvement = None
        if tuned_wape is not None and best_baseline["common_wape"]:
            improvement = round((best_baseline["common_wape"] - tuned_wape) / best_baseline["common_wape"], 6)
        return {
            "champion_adapter_id": best_baseline["adapter_id"],
            "champion_source": "EMPIRICAL_BASELINE_OVERRIDE",
            "kg_model_status": "EXECUTED_NOT_CHAMPION",
            "champion_reason": "BASELINE_BETTER_OR_IMPROVEMENT_BELOW_1PCT",
            "tuned_common_wape": tuned_wape,
            "best_baseline": best_baseline,
            "improvement": improvement,
            "comparison": comparison,
        }
    return {
        "champion_adapter_id": "tuned",
        "champion_source": "KG_RECOMMENDED_MODEL",
        "kg_model_status": "EXECUTED_NO_COMMON_COMPARISON",
        "champion_reason": "NO_COMMON_SAMPLE_AT_ALL",
        "tuned_common_wape": tuned_wape,
        "best_baseline": None,
        "improvement": None,
        "comparison": comparison,
    }


def _persist_learning_cycle(
    conn: Any,
    run_id: str,
    cycle_type: str,
    champion: dict[str, Any],
    evidence: dict[str, Any],
    min_matured: int,
    matured_count: int,
) -> str:
    esha = sha256_json(evidence)
    cycle_id = str(stable_uuid("learning-cycle", f"{run_id}:{cycle_type}"))
    conn.execute(
        f"""
        insert into {SCHEMA}.kg_learning_cycles
            (learning_cycle_id, run_id, recommendation_id, cycle_type, status,
             min_matured_feedback, matured_feedback_count, evidence, evidence_sha256)
        values (%s,%s,%s,%s,'COMPLETED',%s,%s,%s::jsonb,%s)
        on conflict do nothing
        """,
        (
            cycle_id,
            run_id,
            DEMO_RECOMMENDATION_ID,
            cycle_type,
            min_matured,
            matured_count,
            json.dumps(evidence, default=str),
            esha,
        ),
    )
    conn.execute(
        f"""
        insert into {SCHEMA}.kg_champion_decisions
            (decision_id, learning_cycle_id, run_id, champion_adapter_id, champion_source,
             kg_model_status, reason, decided_by, promotion_applied)
        values (%s,%s,%s,%s,%s,%s,%s,'SYSTEM_BACKTEST',false)
        on conflict do nothing
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
    return cycle_id


def _persist_future(
    conn: Any,
    run_id: str,
    champion_adapter: str,
    rows: Sequence[TrainingRow],
    params: dict[str, Any],
    frequency: str,
    seed: int,
    dataset_sha: str,
) -> int:
    """Future H1-H4 from the champion fitted on ALL rows; actuals unknown."""
    issue_period = rows[-1].period_start
    fitted = fit_model(champion_adapter, list(rows), params, seed, issue_period, frequency)
    preds = predict_model(fitted, HORIZONS, issue_period)
    for p in preds:
        target = period_add(issue_period, frequency, p["horizon"])
        vid = _value_id(run_id, target, p["horizon"], ROW_TYPE_FUTURE)
        conn.execute(
            f"""
            insert into {SCHEMA}.kg_forecast_shadow_values
                (value_id, run_id, target_period_start, target_period_end, horizon,
                 yhat, lower_value, upper_value, actual_value, is_observed, is_imputed,
                 source_observation_ids, row_type, properties)
            values (%s,%s,%s,%s,%s,%s,%s,%s,NULL,false,false,'{{}}','FUTURE',%s::jsonb)
            on conflict do nothing
            """,
            (
                vid,
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
                        "data_through_period": issue_period.isoformat(),
                        "feature_max_period": issue_period.isoformat(),
                        "dataset_sha256": dataset_sha,
                        "seed": seed,
                        "model_artifact_sha256": fitted.artifact_sha256,
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
        rsha = sha256_json({"run_id": run_id, "value_id": vid, "kind": ROW_TYPE_FUTURE, "status": MATURITY_AWAITING})
        conn.execute(
            f"""
            insert into {SCHEMA}.kg_forecast_objective_feedback
                (feedback_id, run_id, forecast_value_id, adapter_id, target_period,
                 actual_observation_id, actual_value, actual_revision, actual_available_at,
                 absolute_error, squared_error, interval_hit, direction_correct,
                 feedback_maturity, reconciliation_sha256)
            values (%s,%s,%s,%s,%s,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,%s,%s)
            on conflict do nothing
            """,
            (
                str(stable_uuid("obj-feedback", rsha)),
                run_id,
                vid,
                champion_adapter,
                target,
                MATURITY_AWAITING,
                rsha,
            ),
        )
    return len(preds)


def _champion_params(champion_adapter: str, tuned_adapter: str, tuned_params: dict[str, Any]) -> dict[str, Any]:
    return tuned_params if champion_adapter == tuned_adapter else per_model_params(champion_adapter)


# -------------------------------------------------------------------- shadows
def _insert_shadow_run(
    conn: Any, run_id: str, idem: str, snapshot: dict[str, Any], split: TemporalSplit, status: str
) -> str:
    """Insert the demo shadow run row and return the AUTHORITATIVE run_id.

    kg_forecast_shadow_runs has THREE unique constraints (run_id, run_key,
    idempotency_key) and run_id/run_key are derived differently (run_id from
    the caller's idempotency key, run_key from content only), so a second
    idempotency key with identical content collides on run_key. The bare
    ON CONFLICT DO NOTHING absorbs any of them; the re-select by run_key
    returns the persisted row's id so downstream FK references converge on
    the existing run instead of 500-ing.
    """
    run_key = f"pt-demo:{DEMO_KEY}:{snapshot['snapshot_sha256'][:12]}:c1"
    profile = {
        "pt_demo": True,
        "demo_key": DEMO_KEY,
        "snapshot_sha256": snapshot["snapshot_sha256"],
        "dataset_sha256": snapshot["dataset_sha256"],
        "split_sha256": split.split_sha256,
        "recommendation_id": DEMO_RECOMMENDATION_ID,
        "tuned_adapter": DEMO_ADAPTER_ID,
        "impl_version": PT_IMPL_VERSION,
        "seed": PT_SEED,
        "note": "model_name is the tuning subject adapter; champion of record lives in kg_champion_decisions",
    }
    conn.execute(
        f"""
        insert into {SCHEMA}.kg_forecast_shadow_runs
            (run_id, run_key, idempotency_key, mode, status, disease_slug, country_alpha3,
             metric_code, cadence, data_through, model_name, fallback_reason,
             promotion_eligible, active_pointer_changed, trusted_rows_changed,
             profile, selection_trace)
        values (%s,%s,%s,'SHADOW',%s,%s,%s,%s,%s,%s,%s,%s,false,false,0,%s::jsonb,%s::jsonb)
        on conflict do nothing
        """,
        (
            run_id,
            run_key,
            idem,
            status,
            snapshot["disease"],
            snapshot["country"],
            snapshot["indicator"],
            snapshot["frequency"],
            snapshot["rows"][-1].period_start,
            DEMO_ADAPTER_ID,
            None,
            json.dumps(profile, default=str),
            json.dumps({"shadow_notice": "SHADOW_ONLY_NO_PROMOTION", "replay": REPLAY_KIND}, default=str),
        ),
    )
    existing = conn.execute(
        f"select run_id::text from {SCHEMA}.kg_forecast_shadow_runs where run_key = %s", (run_key,)
    ).fetchone()
    if existing:
        return str(_val(existing, "run_id", 0))
    return run_id


# -------------------------------------------------------------------- driver
def execute_pt_demo(
    conn: Any,
    actor: str = "pt_demo_runner",
    idempotency_key: str = "pt-demo:AUT:legionella:v1",
    seed: int = PT_SEED,
    max_trials: int = MAX_TRIALS,
    max_seconds: int = MAX_SECONDS,
) -> dict[str, Any]:
    """Run the full two-cycle demo. Idempotent: a completed run pair for the
    same idempotency key returns {'status': 'IDEMPOTENT_REPLAY', ...} and
    writes nothing.

    Return dict keys (status='COMPLETED'):
      status, demo_key, idempotency_key, snapshot_id, snapshot_sha256,
      dataset_sha256, split_id, split_sha256, split_sha256_cycle2,
      shadow_run_id, tuning_run_id, tuning_run_id_cycle2, best_trial_id,
      best_trial_index, best_params, n_trials, n_trials_cycle2, stopped_early,
      stopped_early_cycle2, champion (cycle-1 decision dict),
      champion_cycle2 (decision dict), test_evaluations {adapter_id: eval_id},
      feedback {test_awaiting_written, test_matured, test_superseded,
      future_awaiting}, learning_cycle_ids [cycle1, cycle2],
      future_forecast_count, retrain {triggered, newly_matured, min_required,
      reason}, limitations [str].
    For status='IDEMPOTENT_REPLAY' the keys are: status, demo_key,
      idempotency_key, snapshot_sha256, split_sha256, split_sha256_cycle2,
      shadow_run_id, tuning_run_id, tuning_run_id_cycle2, best_trial_id.
    Replay is triggered two ways, both writing nothing: (a) the same
    idempotency key already has both cycles COMPLETED, or (b) a DIFFERENT key
    arrives with identical content (snapshot + both split shas + adapter +
    cycle index) — the existing COMPLETED runs' ids are returned. Every
    insert uses bare ON CONFLICT DO NOTHING and deterministic ids, so a
    collision on ANY unique constraint converges instead of raising; the
    shadow run id is re-selected by run_key after the insert so downstream
    FK references always use the persisted row.
    """
    idem2 = f"{idempotency_key}:cycle2"
    run1 = conn.execute(
        f"select tuning_run_id::text, status, shadow_run_id::text, snapshot_sha256, split_sha256, "
        f"best_trial_id::text from {SCHEMA}.kg_pt_tuning_runs where idempotency_key = %s",
        (idempotency_key,),
    ).fetchone()
    run2 = conn.execute(
        f"select tuning_run_id::text, status, split_sha256 from {SCHEMA}.kg_pt_tuning_runs where idempotency_key = %s",
        (idem2,),
    ).fetchone()
    if run1 and _val(run1, "status", 1) == "COMPLETED" and run2 and _val(run2, "status", 1) == "COMPLETED":
        return {
            "status": "IDEMPOTENT_REPLAY",
            "demo_key": DEMO_KEY,
            "idempotency_key": idempotency_key,
            "snapshot_sha256": _val(run1, "snapshot_sha256", 3),
            "split_sha256": _val(run1, "split_sha256", 4),
            "split_sha256_cycle2": _val(run2, "split_sha256", 2),
            "shadow_run_id": _val(run1, "shadow_run_id", 2),
            "tuning_run_id": _val(run1, "tuning_run_id", 0),
            "tuning_run_id_cycle2": _val(run2, "tuning_run_id", 0),
            "best_trial_id": _val(run1, "best_trial_id", 5),
        }

    # ---------------- cycle 1: snapshot / split / contracts / tuning (pure)
    snapshot = ensure_demo_snapshot(conn)
    rows: list[TrainingRow] = snapshot["rows"]
    periods = [r.period_start for r in rows]
    # Content convergence: identical snapshot+splits under a DIFFERENT
    # idempotency key must replay the already-persisted runs (writing nothing)
    # instead of duplicating them. Checked before any demo-table insert and
    # before the expensive tuning recompute.
    split1 = make_split(periods, TRAIN_N, VAL_N, TEST_N)
    split2 = make_split(periods, TRAIN_N + VAL_N, VAL_N, 0)
    content1 = _find_tuning_run_by_content(
        conn, snapshot["snapshot_sha256"], split1.split_sha256, DEMO_ADAPTER_ID, 1, completed_only=True
    )
    content2 = _find_tuning_run_by_content(
        conn, snapshot["snapshot_sha256"], split2.split_sha256, DEMO_ADAPTER_ID, 2, completed_only=True
    )
    if content1 is not None and content2 is not None:
        return {
            "status": "IDEMPOTENT_REPLAY",
            "demo_key": DEMO_KEY,
            "idempotency_key": idempotency_key,
            "snapshot_sha256": snapshot["snapshot_sha256"],
            "split_sha256": split1.split_sha256,
            "split_sha256_cycle2": split2.split_sha256,
            "shadow_run_id": content1["shadow_run_id"],
            "tuning_run_id": content1["tuning_run_id"],
            "tuning_run_id_cycle2": content2["tuning_run_id"],
            "best_trial_id": content1["best_trial_id"],
        }
    split1 = ensure_split(conn, snapshot["snapshot_sha256"], periods, DEMO_FREQUENCY, TRAIN_N, VAL_N, TEST_N)
    ensure_contracts(conn, DEMO_ADAPTER_ID, DEMO_FREQUENCY)
    contracts = contracts_for(DEMO_ADAPTER_ID, DEMO_FREQUENCY)
    trainval = rows[: TRAIN_N + VAL_N]
    tuning1 = run_tuning(trainval, split1, DEMO_ADAPTER_ID, contracts, seed, max_trials, max_seconds, DEMO_FREQUENCY)
    best_trial = tuning1.trials[tuning1.best_trial_index] if tuning1.best_trial_index is not None else None
    best_params = dict(best_trial.params) if best_trial else default_params(contracts)

    # validation backtest (labels allowed: validation window only)
    models: dict[str, dict[str, Any]] = {DEMO_ADAPTER_ID: {"parameters": best_params}}
    for b in BASELINE_IDS:
        models[b] = {"parameters": {}}
    val_bt = _run_window_backtest(
        trainval, models, tuning_origin_indices(len(trainval), split1), DEMO_FREQUENCY, seed, with_labels=True
    )
    val_bt["metrics"] = {a: _window_metrics(val_bt["per_model"][a]) for a in models}

    # test-window predictions: NO labels read at this stage
    test_bt = _run_window_backtest(
        rows, models, test_origin_indices(len(rows), split1), DEMO_FREQUENCY, seed, with_labels=False
    )

    # ---------------- persist batch 1 (before any test label is read)
    shadow_run_id = _insert_shadow_run(
        conn,
        str(stable_uuid("pt-shadow-run", f"pt-shadow:{idempotency_key}:{snapshot['snapshot_sha256']}")),
        f"pt-shadow:{idempotency_key}",
        snapshot,
        split1,
        "COMPLETED",
    )
    tuning_run_id, best_trial_id = _persist_tuning_run(
        conn,
        str(stable_uuid("pt-tuning-run", idempotency_key)),
        shadow_run_id,
        snapshot["snapshot_sha256"],
        split1.split_sha256,
        DEMO_ADAPTER_ID,
        1,
        idempotency_key,
        tuning1,
    )
    _persist_backtest_ledger(conn, shadow_run_id, val_bt)
    tuned_test_cells = test_bt["per_model"][DEMO_ADAPTER_ID]
    _persist_test_shadow_values(conn, shadow_run_id, tuned_test_cells, snapshot["snapshot_sha256"])

    # ---------------- exactly-once test evaluation on FROZEN labels
    frozen = _load_frozen_rows(conn, snapshot["snapshot_id"])
    eval_ids, common_wapes, _common = _evaluate_test_window(
        conn, tuning_run_id, split1.split_sha256, test_bt["per_model"], frozen, DEMO_ADAPTER_ID, best_params
    )
    feedback = {"test_awaiting_written": len(tuned_test_cells)}
    feedback.update(_mature_test_feedback(conn, shadow_run_id, DEMO_ADAPTER_ID, tuned_test_cells, frozen))
    tuned_gate_pass = bool(best_trial and best_trial.gate_pass)
    champion = _decide_champion(
        common_wapes.get(DEMO_ADAPTER_ID), {b: common_wapes.get(b) for b in BASELINE_IDS}, tuned_gate_pass
    )
    champion_adapter = champion["champion_adapter_id"]
    if champion_adapter == "tuned":
        champion_adapter = DEMO_ADAPTER_ID
    champion["champion_adapter_id"] = champion_adapter

    # future H1-H4 from the champion on all rows
    future_count = _persist_future(
        conn,
        shadow_run_id,
        champion_adapter,
        rows,
        _champion_params(champion_adapter, DEMO_ADAPTER_ID, best_params),
        DEMO_FREQUENCY,
        seed,
        snapshot["dataset_sha256"],
    )
    feedback["future_awaiting"] = future_count

    evidence1 = {
        "run_id": shadow_run_id,
        "tuning_run_id": tuning_run_id,
        "recommendation_id": DEMO_RECOMMENDATION_ID,
        "replay": REPLAY_KIND,
        "objective": tuning1.objective,
        "best_trial_index": tuning1.best_trial_index,
        "best_params": best_params,
        "test_common_wape": common_wapes,
        "champion": champion,
        "feedback": feedback,
        "learning_rule": "CONTROLLED_BATCH_LEARNING",
        "limitations": LIMITATIONS,
    }
    cycle1_id = _persist_learning_cycle(
        conn, shadow_run_id, CYCLE_TYPE_EVALUATION, champion, evidence1, 1, feedback.get("test_matured", 0)
    )

    # ---------------- cycle 2: controlled retrain (historical replay)
    newly_matured = feedback.get("test_matured", 0)
    triggered = newly_matured >= RETRAIN_MIN_MATURED
    retrain_reason = "MATURED_FEEDBACK_THRESHOLD_REACHED" if triggered else "INSUFFICIENT_MATURED_FEEDBACK"
    snapshot2 = ensure_demo_snapshot(conn)  # identical rows -> same sha, reused
    split2 = ensure_split(conn, snapshot2["snapshot_sha256"], periods, DEMO_FREQUENCY, TRAIN_N + VAL_N, VAL_N, 0)
    tuning2 = run_tuning(rows, split2, DEMO_ADAPTER_ID, contracts, seed, max_trials, max_seconds, DEMO_FREQUENCY)
    tuning_run_id2, _best_trial_id2 = _persist_tuning_run(
        conn,
        str(stable_uuid("pt-tuning-run", idem2)),
        shadow_run_id,
        snapshot2["snapshot_sha256"],
        split2.split_sha256,
        DEMO_ADAPTER_ID,
        2,
        idem2,
        tuning2,
    )
    # challenger (cycle-2 best) vs incumbent champion on identical origins
    origins2 = tuning_origin_indices(len(rows), split2)
    challenger_params = (
        dict(tuning2.trials[tuning2.best_trial_index].params) if tuning2.best_trial_index is not None else None
    )
    champion_cycle2: dict[str, Any]
    if not triggered or challenger_params is None:
        champion_cycle2 = {
            "champion_adapter_id": champion_adapter,
            "champion_source": champion["champion_source"],
            "kg_model_status": champion["kg_model_status"],
            "champion_reason": "INCUMBENT_RETAINED_RETRAIN_NOT_TRIGGERED"
            if not triggered
            else "INCUMBENT_RETAINED_CHALLENGER_FAILED_ALL_TRIALS",
            "challenger_common_wape": None,
            "incumbent_common_wape": None,
            "improvement": None,
        }
    else:
        cmp_models = {
            "challenger": {"fit_as": DEMO_ADAPTER_ID, "parameters": challenger_params},
            "incumbent": {
                "fit_as": champion_adapter,
                "parameters": _champion_params(champion_adapter, DEMO_ADAPTER_ID, best_params),
            },
        }
        cmp_bt = _run_window_backtest(rows, cmp_models, origins2, DEMO_FREQUENCY, seed, with_labels=True)
        cmp_common: set[Any] | None = None
        for cells in cmp_bt["per_model"].values():
            keys = {
                (c["origin_index"], c["horizon"])
                for c in cells
                if c["yhat"] is not None and c["actual_value"] is not None
            }
            cmp_common = keys if cmp_common is None else (cmp_common & keys)
        cmp_common = cmp_common or set()

        def _cmp_wape(label: str) -> float | None:
            errors = actuals = 0.0
            n = 0
            for c in cmp_bt["per_model"][label]:
                if (c["origin_index"], c["horizon"]) not in cmp_common:
                    continue
                errors += abs(c["yhat"] - c["actual_value"])
                actuals += abs(c["actual_value"])
                n += 1
            return errors / actuals if n and actuals > 0 else None

        challenger_wape = _cmp_wape("challenger")
        incumbent_wape = _cmp_wape("incumbent")
        improvement = None
        if challenger_wape is not None and incumbent_wape:
            improvement = (incumbent_wape - challenger_wape) / incumbent_wape
        if improvement is not None and improvement >= CHAMPION_MIN_IMPROVEMENT:
            champion_cycle2 = {
                "champion_adapter_id": DEMO_ADAPTER_ID,
                "champion_source": "KG_RECOMMENDED_MODEL",
                "kg_model_status": "EXECUTED_CHAMPION",
                "champion_reason": "CHALLENGER_COMMON_WAPE_IMPROVEMENT_GE_1PCT",
                "challenger_common_wape": challenger_wape,
                "incumbent_common_wape": incumbent_wape,
                "improvement": round(improvement, 6),
            }
        else:
            champion_cycle2 = {
                "champion_adapter_id": champion_adapter,
                "champion_source": champion["champion_source"],
                "kg_model_status": champion["kg_model_status"],
                "champion_reason": "INCUMBENT_RETAINED_CHALLENGER_NOT_SIGNIFICANTLY_BETTER",
                "challenger_common_wape": challenger_wape,
                "incumbent_common_wape": incumbent_wape,
                "improvement": round(improvement, 6) if improvement is not None else None,
            }
    evidence2 = {
        "run_id": shadow_run_id,
        "tuning_run_id": tuning_run_id2,
        "replay": REPLAY_KIND,
        "replay_notice": "This cycle is a DEMO_HISTORICAL_REPLAY of already-observed periods, not a live event.",
        "heldout": NO_REMAINING_HELDOUT,
        "trigger": {
            "triggered": triggered,
            "newly_matured": newly_matured,
            "min_required": RETRAIN_MIN_MATURED,
            "revision_changed": False,
            "drift_assessment": "NOT_COMPUTED_DEMO_REPLAY",
            "performance_trigger": "matured_feedback>=RETRAIN_MIN_MATURED",
            "reason": retrain_reason,
        },
        "snapshot_reused": snapshot2["snapshot_sha256"] == snapshot["snapshot_sha256"],
        "split_sha256": split2.split_sha256,
        "champion": champion_cycle2,
        "learning_rule": "CONTROLLED_BATCH_LEARNING",
        "limitations": LIMITATIONS,
    }
    cycle2_id = _persist_learning_cycle(
        conn, shadow_run_id, CYCLE_TYPE_RETRAIN, champion_cycle2, evidence2, RETRAIN_MIN_MATURED, newly_matured
    )

    return {
        "status": "COMPLETED",
        "demo_key": DEMO_KEY,
        "idempotency_key": idempotency_key,
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_sha256": snapshot["snapshot_sha256"],
        "dataset_sha256": snapshot["dataset_sha256"],
        "split_id": str(stable_uuid("pt-split", split1.split_sha256)),
        "split_sha256": split1.split_sha256,
        "split_sha256_cycle2": split2.split_sha256,
        "shadow_run_id": shadow_run_id,
        "tuning_run_id": tuning_run_id,
        "tuning_run_id_cycle2": tuning_run_id2,
        "best_trial_id": best_trial_id,
        "best_trial_index": tuning1.best_trial_index,
        "best_params": best_params,
        "n_trials": len(tuning1.trials),
        "n_trials_cycle2": len(tuning2.trials),
        "stopped_early": tuning1.stopped_early,
        "stopped_early_cycle2": tuning2.stopped_early,
        "champion": champion,
        "champion_cycle2": champion_cycle2,
        "test_evaluations": eval_ids,
        "feedback": feedback,
        "learning_cycle_ids": [cycle1_id, cycle2_id],
        "future_forecast_count": future_count,
        "retrain": {
            "triggered": triggered,
            "newly_matured": newly_matured,
            "min_required": RETRAIN_MIN_MATURED,
            "reason": retrain_reason,
        },
        "limitations": LIMITATIONS,
    }


# --------------------------------------------------------- read-side helpers
def pt_overview(conn: Any, demo_key: str = DEMO_KEY) -> dict[str, Any] | None:
    """Aggregate read for the API overview endpoint (read-only)."""
    snap = conn.execute(
        f"select snapshot_id::text, demo_key, n_obs, first_period, last_period, data_through, "
        f"missing_periods, missing_semantics, dataset_sha256, snapshot_sha256 "
        f"from {SCHEMA}.kg_pt_demo_snapshots where demo_key = %s order by created_at desc limit 1",
        (demo_key,),
    ).fetchone()
    if not snap:
        return None
    snap_cols = [
        "snapshot_id",
        "demo_key",
        "n_obs",
        "first_period",
        "last_period",
        "data_through",
        "missing_periods",
        "missing_semantics",
        "dataset_sha256",
        "snapshot_sha256",
    ]
    snapshot = {c: _val(snap, c, i) for i, c in enumerate(snap_cols)}
    runs = conn.execute(
        f"select tuning_run_id::text, cycle_index, status, seed, max_trials, max_seconds, objective, "
        f"code_version, best_trial_id::text, shadow_run_id::text, split_sha256, started_at, completed_at "
        f"from {SCHEMA}.kg_pt_tuning_runs where snapshot_sha256 = %s order by cycle_index",
        (snapshot["snapshot_sha256"],),
    ).fetchall()
    run_cols = [
        "tuning_run_id",
        "cycle_index",
        "status",
        "seed",
        "max_trials",
        "max_seconds",
        "objective",
        "code_version",
        "best_trial_id",
        "shadow_run_id",
        "split_sha256",
        "started_at",
        "completed_at",
    ]
    tuning_runs = [{c: _val(r, c, i) for i, c in enumerate(run_cols)} for r in runs]
    evaluations: list[dict[str, Any]] = []
    cycles: list[dict[str, Any]] = []
    if tuning_runs:
        eval_rows = conn.execute(
            f"select eval_id::text, adapter_id, is_tuned_model, mae, rmse, wape, smape, mase, coverage, "
            f"avg_interval_width, agreement_pct from {SCHEMA}.kg_pt_test_evaluations "
            f"where tuning_run_id = %s order by adapter_id",
            (tuning_runs[0]["tuning_run_id"],),
        ).fetchall()
        eval_cols = [
            "eval_id",
            "adapter_id",
            "is_tuned_model",
            "mae",
            "rmse",
            "wape",
            "smape",
            "mase",
            "coverage",
            "avg_interval_width",
            "agreement_pct",
        ]
        evaluations = [{c: _val(r, c, i) for i, c in enumerate(eval_cols)} for r in eval_rows]
        shadow_run_id = tuning_runs[0]["shadow_run_id"]
        if shadow_run_id:
            cyc_rows = conn.execute(
                f"select l.learning_cycle_id::text, l.cycle_type, l.status, l.matured_feedback_count, "
                f"c.champion_adapter_id, c.champion_source, c.kg_model_status, c.reason, c.promotion_applied "
                f"from {SCHEMA}.kg_learning_cycles l "
                f"left join {SCHEMA}.kg_champion_decisions c on c.learning_cycle_id = l.learning_cycle_id "
                f"where l.run_id = %s order by l.created_at",
                (shadow_run_id,),
            ).fetchall()
            cyc_cols = [
                "learning_cycle_id",
                "cycle_type",
                "status",
                "matured_feedback_count",
                "champion_adapter_id",
                "champion_source",
                "kg_model_status",
                "reason",
                "promotion_applied",
            ]
            cycles = [{c: _val(r, c, i) for i, c in enumerate(cyc_cols)} for r in cyc_rows]
    return {
        "snapshot": snapshot,
        "tuning_runs": tuning_runs,
        "test_evaluations": evaluations,
        "learning_cycles": cycles,
        "limitations": LIMITATIONS,
    }


def pt_trials(conn: Any, tuning_run_id: str) -> list[dict[str, Any]]:
    """All trials (including failures) for one tuning run (read-only)."""
    rows = conn.execute(
        f"select trial_id::text, trial_index, params, parameter_source, n_origins, n_labeled, common_wape, "
        f"per_horizon_metrics, coverage, avg_interval_width, complexity_penalty, objective_value, gates, "
        f"gate_pass, failure_reason, elapsed_ms, artifact_sha256, trial_sha256 "
        f"from {SCHEMA}.kg_pt_tuning_trials where tuning_run_id = %s order by trial_index",
        (tuning_run_id,),
    ).fetchall()
    cols = [
        "trial_id",
        "trial_index",
        "params",
        "parameter_source",
        "n_origins",
        "n_labeled",
        "common_wape",
        "per_horizon_metrics",
        "coverage",
        "avg_interval_width",
        "complexity_penalty",
        "objective_value",
        "gates",
        "gate_pass",
        "failure_reason",
        "elapsed_ms",
        "artifact_sha256",
        "trial_sha256",
    ]
    return [{c: _val(r, c, i) for i, c in enumerate(cols)} for r in rows]


def pt_forecast(conn: Any, run_id: str) -> dict[str, Any]:
    """Test-window + future forecast rows for charts (read-only)."""
    rows = conn.execute(
        f"select target_period_start, horizon, yhat, lower_value, upper_value, actual_value, row_type, "
        f"properties from {SCHEMA}.kg_forecast_shadow_values where run_id = %s "
        f"order by target_period_start, horizon",
        (run_id,),
    ).fetchall()
    cols = [
        "target_period_start",
        "horizon",
        "yhat",
        "lower_value",
        "upper_value",
        "actual_value",
        "row_type",
        "properties",
    ]
    values = [{c: _val(r, c, i) for i, c in enumerate(cols)} for r in rows]
    return {"run_id": run_id, "values": values, "limitations": LIMITATIONS}
