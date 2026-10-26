"""Database orchestration for the Qwen-planned autotuning + feedback loop.

Flow of ``execute_autotuning`` (all Shadow, nothing trusted is ever written):

    snapshot freeze (reuses the kg_pt snapshot machinery; Track B freezes a
    new FIN snapshot) -> Track B: lock the hidden eval window BEFORE any
    Qwen call or any tuning (kg_qwen_evaluation_sets, FRESH_HELDOUT_LOCKED,
    eval_count=0) -> data profile + contract upserts (kg_pt_parameter_contracts,
    additive) -> create/reuse kg_qwen_agent_runs (idempotency_key unique;
    replay returns the existing run summary) -> ToolContext + planner
    (qwen_client + agent_tools; QwenUnavailable -> deterministic fallback plan)
    -> run_autotuning_loop with an on_round_complete persistence callback
    (plans/rounds/trials) -> champion -> exactly-once held-out evaluation
    (Track B consumes eval_count 0->1 atomically; Track A is marked
    REUSED_HISTORICAL_BENCHMARK + NOT_FRESH_HELDOUT) -> Shadow publication
    (kg_forecast_shadow_runs/values: H1-H4 future + replay window with
    actual=NULL and AWAITING_ACTUAL feedback rows) -> DEMO_HISTORICAL_REPLAY
    maturation -> kg_learning_cycles + kg_champion_decisions -> target_status
    written back onto the run -> summary dict.

Idempotency: stable_uuid content-addressed ids + bare ON CONFLICT DO NOTHING
everywhere; tuning plans and trials are INSERT-only (never updated).

This module is importable without a database or psycopg: all DB access
happens inside functions, the kg_qwen sub-modules are imported lazily (the
local integration workspace may stub them), and row access supports both
psycopg dict_row and tuple rows.
"""

from __future__ import annotations

import contextlib
import io
import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, cast

from epidemic_core.kg_pt import DEMO_RECOMMENDATION_ID, HORIZONS, RETRAIN_MIN_MATURED
from epidemic_core.kg_pt import pipeline as pt_pipeline
from epidemic_core.kg_pt.metrics import agreement_pct, cell_metrics
from epidemic_core.kg_pt.split import TemporalSplit
from epidemic_core.kg_r3c.execution import (
    BASELINE_IDS,
    TrainingRow,
    sha256_json,
    sha256_text,
    stable_uuid,
)

SCHEMA = "epidemic_kg"
SOURCE_TABLE = "epidemic.dashboard_history_series_v2"

TRACK_A_KEY = "AUT:legionella:legionnaires_reported_cases:month"
TRACK_B_KEY = "FIN:hantavirus:puumala_virus_reports:week"
TRACK_C_KEY = "CHL:influenza:influenza_positive_detections:week"

# Track A: identical geometry to the kg_pt demo (train 168 / val 36 / test 36).
TRACK_A = {
    "key": TRACK_A_KEY,
    "frequency": "month",
    "train_n": 168,
    "val_n": 36,
    "test_n": 36,
    "hidden_n": 0,
    "eval_identity": "REUSED_HISTORICAL_BENCHMARK",
    "eval_flags": ["REUSED_HISTORICAL_BENCHMARK", "NOT_FRESH_HELDOUT"],
}
# Track B: 445 weekly observations; tuning window = first 393 rows
# (train 321 / val 72 / test 0), hidden eval = last 52 rows, locked upfront.
TRACK_B = {
    "key": TRACK_B_KEY,
    "frequency": "week",
    "train_n": 321,
    "val_n": 72,
    "test_n": 0,
    "hidden_n": 52,
    "tuning_n": 393,
    "eval_identity": "FRESH_HELDOUT_LOCKED",
    "eval_flags": ["FRESH_HELDOUT_LOCKED"],
}
TRACKS = {TRACK_A_KEY: TRACK_A, TRACK_B_KEY: TRACK_B}

# Track C: CHL/influenza/influenza_positive_detections/week — verified fresh
# blind-window candidate (governance repair, 2026-08-12):
#   * sourced from epidemic.normalized_observations (FluNet detections), NOT
#     from dashboard_history_series_v2 (which only carries AUT+FIN);
#   * zero usage anywhere before this run: 0 rows in training_observations_v1,
#     0 forecast_runs / forecast_values / prediction_results, no kg shadow
#     runs, no kg_data_snapshots, no earlier flunet training (the local
#     p1_18_flunet artifact was blocked and never trained);
#   * 1580 weekly rows (1997-04..2026-07); the trailing 445 rows are used:
#     tuning window = first 393 (train 321 / val 72), hidden window = last
#     52 weeks (2025-07-21..2026-07-13), globally locked before any Qwen call
#     and consumable exactly once across ALL runs.
# DEU/legionella/month was evaluated as a candidate and REJECTED: its full
# 2005-2024 series (including the 2022-2024 window) is already inside 5
# main-platform dataset snapshots with forecast_values, i.e. previously opened.
TRACK_C = {
    "key": TRACK_C_KEY,
    "frequency": "week",
    "train_n": 321,
    "val_n": 72,
    "test_n": 0,
    "hidden_n": 52,
    "tuning_n": 393,
    "total_n": 445,
    "eval_identity": "FRESH_HELDOUT_LOCKED",
    "eval_flags": ["FRESH_HELDOUT_LOCKED"],
    "source_override": {
        "table": "epidemic.normalized_observations",
        "country_alpha2": "CL",
        "disease_id": "6c9e2d3e-3a33-525a-8fbf-2b2eb1db4982",
        "metric_code": "influenza_positive_detections",
        "granularity": "week",
    },
}
TRACKS[TRACK_C_KEY] = TRACK_C

# ---------------------------------------------------------------- held-out governance
HELDOUT_EVALUATOR_VERSION = "kg_qwen_heldout_evaluator.v2"
REGISTRY_STATUSES = ("REGISTERED", "LOCKED_UNOPENED", "CONSUMED", "INVALIDATED", "REPLAY_ONLY")
# Identities that may be re-evaluated for retrospective demos but never
# certified as fresh held-out.
REPLAY_IDENTITIES = ("REUSED_HISTORICAL_BENCHMARK", "RETROSPECTIVE_REPLAY_NOT_FRESH")
IDENTITY_RETROSPECTIVE = "RETROSPECTIVE_REPLAY_NOT_FRESH"

TRACK_B_TUNING_WEEKS = 393
TRACK_B_HIDDEN_WEEKS = 52
TRACK_B_TOTAL_WEEKS = TRACK_B_TUNING_WEEKS + TRACK_B_HIDDEN_WEEKS  # 445

# Fallback when the kg_qwen package constants are unavailable (local stubs).
KQ_SEED_DEFAULT = 20260811

TARGET_STATUS_MET = "TARGET_MET"
TARGET_STATUS_NOT_MET = "AUTOTUNING_TARGET_NOT_MET_WITHIN_BUDGET"

EVAL_KIND_REUSED = "REUSED"
EVAL_KIND_HIDDEN = "HIDDEN"

MATURITY_AWAITING = pt_pipeline.MATURITY_AWAITING
MATURITY_MATURED = pt_pipeline.MATURITY_MATURED
MATURITY_SUPERSEDED = pt_pipeline.MATURITY_SUPERSEDED

ROW_TYPE_TEST_REPLAY = pt_pipeline.ROW_TYPE_TEST_REPLAY
ROW_TYPE_FUTURE = pt_pipeline.ROW_TYPE_FUTURE

CYCLE_TYPE_EVALUATION = "AUTOTUNING_EVALUATION"
CYCLE_TYPE_RECONCILE = "AUTOTUNING_FEEDBACK_RECONCILE"

LIMITATIONS = [
    "全部结果处于 Shadow 状态：未发布、未晋级、不影响任何正式预测（SHADOW_ONLY_NO_PROMOTION）。",
    "测试/回放窗口的实际值来自历史数据回放（DEMO_HISTORICAL_REPLAY），不是实时到达的新数据。",
    "预测吻合度是基于 WAPE 换算的易读指标（max(0,1-WAPE)×100），不是分类准确率，也不代表未来保证准确。",
    "Track A 使用已被查看过的历史基准测试窗（REUSED_HISTORICAL_BENCHMARK / NOT_FRESH_HELDOUT），"
    "其指标不得称为前瞻准确率。",
    "Track B 的隐藏评估窗曾被 3 个 run 重复评估，已按治理修复归类为 RETROSPECTIVE_REPLAY_NOT_FRESH，"
    "其指标（含 71.97% 吻合度）仅为历史重复评估结果，不构成独立盲测认证。",
    "全局 held-out 注册表以 track+快照+窗口+身份+切分版本为唯一身份（不含 run_id），"
    "跨所有 run 原子消费一次；重复打开返回 HELDOUT_ALREADY_CONSUMED。",
    "本系统是决策支持工具，不构成医疗或公共卫生建议。",
]


class HiddenEvalConsumed(RuntimeError):
    """Raised when a hidden evaluation set has already been evaluated once."""


# ---------------------------------------------------------------- row access
def _val(row: Any, key: str, idx: int) -> Any:
    """Read a column from either a psycopg dict_row or a tuple row."""
    if row is None:
        return None
    if isinstance(row, Mapping):
        return row[key]
    return row[idx]


def _track(track_key: str) -> dict[str, Any]:
    track = TRACKS.get(track_key)
    if track is None:
        raise ValueError(f"KQ_UNKNOWN_TRACK:{track_key}")
    return track


# ------------------------------------------------------------- lazy submodules
def _kq_constants() -> dict[str, Any]:
    """kg_qwen package constants, resolved lazily (SPEC section 3.1)."""
    import importlib

    mod = importlib.import_module("epidemic_core.kg_qwen")
    return {
        "KQ_IMPL_VERSION": getattr(mod, "KQ_IMPL_VERSION", "kg_qwen.v1"),
        "KQ_SEED": getattr(mod, "KQ_SEED", 20260811),
        "TARGET_WAPE": getattr(mod, "TARGET_WAPE", 0.30),
        "TARGET_AGREEMENT_PCT": getattr(mod, "TARGET_AGREEMENT_PCT", 70.0),
        "COVERAGE_MIN": getattr(mod, "COVERAGE_MIN", 0.85),
        "MAX_ROUNDS": getattr(mod, "MAX_ROUNDS", 3),
        "MAX_TRIALS_PER_ROUND": getattr(mod, "MAX_TRIALS_PER_ROUND", 50),
        "MAX_TOTAL_MINUTES": getattr(mod, "MAX_TOTAL_MINUTES", 45),
        "QWEN_MODEL_ID": getattr(mod, "QWEN_MODEL_ID", "Qwen/Qwen3-4B-Instruct-2507"),
        "QWEN_RUNTIME_LABEL": getattr(mod, "QWEN_RUNTIME_LABEL", None),
        "REPLAY_KIND": getattr(mod, "REPLAY_KIND", "DEMO_HISTORICAL_REPLAY"),
    }


def _kq_module(name: str) -> Any:
    import importlib

    return importlib.import_module(f"epidemic_core.kg_qwen.{name}")


# ------------------------------------------------------- pure payload builders
def track_eval_window_payload(track_key: str, periods: Sequence[Any]) -> dict[str, Any]:
    """Pure description of the evaluation window for one track.

    Track A: the trailing 36-period test window (reused historical benchmark).
    Track B: the trailing 52-week hidden window (fresh held-out, locked).
    """
    track = _track(track_key)
    n_eval = track["test_n"] or track["hidden_n"]
    return {
        "identity": track["eval_identity"],
        "flags": list(track["eval_flags"]),
        "start_period": periods[len(periods) - n_eval].isoformat(),
        "end_period": periods[-1].isoformat(),
        "n_periods": n_eval,
        "frequency": track["frequency"],
    }


def budgets_payload(constants: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Pure budget record persisted on the agent run (SPEC section 2)."""
    c = dict(constants or _kq_constants())
    return {
        "max_rounds": c["MAX_ROUNDS"],
        "max_trials_per_round": c["MAX_TRIALS_PER_ROUND"],
        "max_total_minutes": c["MAX_TOTAL_MINUTES"],
        "trial_timeout_seconds": 120,
        "seed": c["KQ_SEED"],
        "target_wape": c["TARGET_WAPE"],
        "target_agreement_pct": c["TARGET_AGREEMENT_PCT"],
        "coverage_min": c["COVERAGE_MIN"],
    }


def run_row_payload(track_key: str, snapshot_sha256: str, constants: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Pure content payload for one agent run (hash input + summary fields)."""
    c = dict(constants or _kq_constants())
    return {
        "track_key": track_key,
        "snapshot_sha256": snapshot_sha256,
        "budgets": budgets_payload(c),
        "code_version": c["KQ_IMPL_VERSION"],
        "qwen_model_id": c["QWEN_MODEL_ID"],
    }


def plan_row_payload(run_id: str, round_index: int, round_result: Any) -> dict[str, Any]:
    """Pure DB-row payload for one tuning plan (hash input + insert values)."""
    proposal = round_result.proposal
    return {
        "run_id": run_id,
        "round_index": round_index,
        "plan_source": proposal.plan_source,
        "plan_json": proposal.plan,
        "plan_sha256": sha256_json(proposal.plan),
        "prompt_sha256": proposal.prompt_sha256,
        "response_sha256": proposal.response_sha256,
        "qwen_latency_ms": proposal.latency_ms,
        "validation_status": round_result.validation_status,
        "validation_errors": list(round_result.validation_errors or []),
    }


def round_row_payload(run_id: str, round_result: Any, best_trial_id: str | None) -> dict[str, Any]:
    """Pure DB-row payload for one tuning round (hash input + insert values)."""
    return {
        "run_id": run_id,
        "round_index": round_result.round_index,
        "status": "COMPLETED",
        "trials_executed": round_result.trials_executed,
        "best_adapter_id": round_result.best_adapter_id,
        "best_trial_id": best_trial_id,
        "best_val_wape": round_result.best_val_wape,
        "best_val_agreement_pct": round_result.best_val_agreement_pct,
        "best_val_coverage": round_result.best_val_coverage,
        "baseline_best_wape": round_result.baseline_best_wape,
        "improvement_vs_baseline": round_result.improvement_vs_baseline,
        "target_met": bool(round_result.target_met),
        "stop_reason": round_result.stop_reason,
    }


def trial_row_payload(run_id: str, round_index: int, adapter_id: str, trial: Any) -> dict[str, Any]:
    """Pure DB-row payload for one trial (hash input + insert values).

    Missing metric keys degrade to None instead of raising so that baseline
    evaluation trials and foreign TrialResult shapes both persist cleanly."""
    origin_shas = [getattr(o, "origin_sha256", None) for o in (getattr(trial, "origins", None) or [])]
    wape = getattr(trial, "common_wape", None)
    return {
        "run_id": run_id,
        "round_index": round_index,
        "trial_index": getattr(trial, "trial_index", 0),
        "adapter_id": adapter_id,
        "params": getattr(trial, "params", {}) or {},
        "params_sha256": getattr(trial, "params_sha256", None) or sha256_json(getattr(trial, "params", {}) or {}),
        "parameter_source": getattr(trial, "parameter_source", {}) or {},
        "origin_sha256s": origin_shas,
        "per_horizon_metrics": getattr(trial, "per_horizon_metrics", {}) or {},
        "common_wape": wape,
        "agreement_pct": agreement_pct(wape),
        "coverage": getattr(trial, "coverage", None),
        "avg_interval_width": getattr(trial, "avg_interval_width", None),
        "complexity_penalty": getattr(trial, "complexity_penalty", None),
        "objective_value": getattr(trial, "objective_value", None),
        "gates": getattr(trial, "gates", {}) or {},
        "gate_pass": bool(getattr(trial, "gate_pass", False)),
        "failure_reason": getattr(trial, "failure_reason", None),
        "artifact_sha256": getattr(trial, "artifact_sha256", None),
        "dataset_sha256": getattr(trial, "dataset_snapshot_sha256", None),
        "trial_sha256": getattr(trial, "trial_sha256", None)
        or sha256_json(
            {
                "run_id": run_id,
                "round_index": round_index,
                "adapter_id": adapter_id,
                "trial_index": getattr(trial, "trial_index", 0),
                "params": getattr(trial, "params", {}) or {},
            }
        ),
        "elapsed_ms": getattr(trial, "elapsed_ms", None),
    }


def _eval_metrics_payload(cells: list[dict[str, Any]]) -> dict[str, Any]:
    """Pure per-horizon + overall metrics over labeled cells."""
    per_horizon: dict[str, Any] = {}
    for horizon in [0] + list(HORIZONS):
        hcells = [c for c in cells if horizon == 0 or c["horizon"] == horizon]
        m = cell_metrics([(c["yhat"], c["actual_value"], c["lower_bound"], c["upper_bound"]) for c in hcells])
        terms = [
            abs(c["yhat"] - c["actual_value"]) / c["mase_denominator"]
            for c in hcells
            if c["yhat"] is not None and c["actual_value"] is not None and c["mase_denominator"]
        ]
        m["mase"] = sum(terms) / len(terms) if terms else None
        per_horizon[str(horizon)] = m
    overall = per_horizon["0"]
    return {
        "per_horizon": per_horizon,
        "mae": overall["mae"],
        "rmse": overall["rmse"],
        "wape": overall["wape"],
        "smape": overall["smape"],
        "mase": overall["mase"],
        "coverage": overall["coverage"],
        "avg_interval_width": overall["avg_width"],
        "agreement_pct": agreement_pct(overall["wape"]),
        "labeled_cells": overall["n"],
    }


def _champion_cells_payload(
    rows: Sequence[TrainingRow],
    adapter_id: str,
    params: dict[str, Any],
    origin_indices: list[int],
    frequency: str,
    seed: int,
    with_labels: bool,
) -> list[dict[str, Any]]:
    """Champion-model predictions on the given origins (labels optional)."""
    # extended adapters register additively; baselines/negbin are built in
    with contextlib.suppress(Exception):
        _kq_module("adapters_ext").register_extended_models()
    models = {adapter_id: {"parameters": params}}
    bt = pt_pipeline._run_window_backtest(rows, models, origin_indices, frequency, seed, with_labels=with_labels)
    return cast("list[dict[str, Any]]", bt["per_model"][adapter_id])


# ------------------------------------------------------------ snapshot / split
def _load_normalized_rows(conn: Any, source: Mapping[str, Any]) -> list[TrainingRow]:
    """Latest-revision rows for a normalized_observations series (Track C).

    Dedupe rule: one row per period_start, highest revision wins, latest
    as_of breaks ties. Missing values stay None; nothing is imputed. Read-only
    on the trusted normalized layer."""
    rows = conn.execute(
        """
        select id::text, period_start, period_end, value_numeric, revision, as_of
        from epidemic.normalized_observations
        where country_alpha2 = %s and disease_id = %s and metric_code = %s and granularity = %s
        order by period_start, revision desc, as_of desc
        """,
        (
            source["country_alpha2"],
            source["disease_id"],
            source["metric_code"],
            source["granularity"],
        ),
    ).fetchall()
    seen: set[Any] = set()
    out: list[TrainingRow] = []
    for r in rows:
        period = _val(r, "period_start", 1)
        if period in seen:
            continue
        seen.add(period)
        out.append(
            TrainingRow(
                period_start=period,
                period_end=_val(r, "period_end", 2),
                value=None if _val(r, "value_numeric", 3) is None else float(_val(r, "value_numeric", 3)),
                observation_id=_val(r, "id", 0),
                revision=int(_val(r, "revision", 4) or 1),
            )
        )
    return out


def _ensure_track_snapshot(conn: Any, track: dict[str, Any]) -> dict[str, Any]:
    """Freeze (or reuse) the track snapshot via the kg_pt snapshot machinery.

    Tracks with a ``source_override`` (Track C: normalized_observations) load
    their rows from that trusted table and freeze the trailing ``total_n``
    rows through the same content-addressed, insert-only snapshot path."""
    override = track.get("source_override")
    if override:
        rows = _load_normalized_rows(conn, override)
        total = int(track.get("total_n") or 0)
        if total:
            rows = rows[-total:]
        if not rows:
            raise ValueError(f"KQ_TRACK_NO_SOURCE_ROWS:{track['key']}")
        return pt_pipeline.ensure_demo_snapshot(
            conn, track["key"], rows_override=rows, source_table_override=str(override["table"])
        )
    return pt_pipeline.ensure_demo_snapshot(conn, track["key"])


def _ensure_track_split(conn: Any, track: dict[str, Any], snapshot: dict[str, Any]) -> TemporalSplit:
    """Content-addressed split upsert.

    Track A: 168/36/36 over all 240 periods. Track B: 321/72/0 over the first
    393 (tuning window) periods only — the hidden 52 weeks never enter the
    split."""
    periods = [r.period_start for r in snapshot["rows"]]
    if track["hidden_n"]:
        periods = periods[: track["tuning_n"]]
    return pt_pipeline.ensure_split(
        conn,
        snapshot["snapshot_sha256"],
        periods,
        track["frequency"],
        track["train_n"],
        track["val_n"],
        track["test_n"],
    )


def _ensure_contracts(conn: Any, frequency: str) -> None:
    """Upsert parameter contracts for every registered adapter (additive)."""
    try:
        contracts_ext = _kq_module("contracts_ext")
        adapter_ids = contracts_ext.registered_adapters()
        contracts_for = contracts_ext.all_contracts_for
    except Exception:
        # contracts_ext not available (local integration): fall back to the
        # kg_pt contracts for the known adapters.
        from epidemic_core.kg_pt.contracts import contracts_for as _kg_pt_contracts_for

        adapter_ids = ["negbin_dglm.v1", *BASELINE_IDS]

        def contracts_for(adapter_id: str, freq: str) -> Any:
            try:
                return _kg_pt_contracts_for(adapter_id, freq)
            except Exception:
                return []

    for adapter_id in adapter_ids:
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


# ------------------------------------------------------- held-out governance
def heldout_identity_payload(
    track: dict[str, Any], snapshot_sha256: str, window: dict[str, Any], split_sha256: str
) -> dict[str, Any]:
    """Global evaluation identity — stable across runs, deliberately WITHOUT
    run_id: the same data snapshot + window + identity + split version maps to
    exactly one registry row no matter how many runs request it."""
    canonical_window = {
        k: window.get(k) for k in ("identity", "flags", "start_period", "end_period", "n_periods", "frequency")
    }
    return {
        "track_key": track["key"],
        "snapshot_sha256": snapshot_sha256,
        "eval_window": canonical_window,
        "identity": track["eval_identity"],
        "split_version": split_sha256,
    }


def heldout_registry_id(payload: dict[str, Any]) -> str:
    return str(stable_uuid("kq-heldout-registry", sha256_json(payload)))


def _registry_row_dict(row: Any) -> dict[str, Any]:
    cols = (
        "registry_id",
        "global_sha256",
        "track_key",
        "identity",
        "snapshot_sha256",
        "split_version",
        "status",
        "registered_at",
        "locked_at",
        "first_qwen_started_at",
        "tuning_finished_at",
        "consumed_at",
        "consumer_run_id",
        "evaluator_version",
    )
    return {c: _val(row, c, i) for i, c in enumerate(cols)}


_REGISTRY_SELECT = (
    "select registry_id::text, global_sha256, track_key, identity, snapshot_sha256, split_version, "
    "status, registered_at, locked_at, first_qwen_started_at, tuning_finished_at, consumed_at, "
    "consumer_run_id::text, evaluator_version"
)


def _fetch_registry(conn: Any, registry_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        f"{_REGISTRY_SELECT} from {SCHEMA}.kg_qwen_heldout_registry where registry_id = %s",
        (registry_id,),
    ).fetchone()
    return _registry_row_dict(row) if row else None


def _register_heldout_window(
    conn: Any, track: dict[str, Any], snapshot: dict[str, Any], window: dict[str, Any], split: TemporalSplit
) -> dict[str, Any]:
    """Insert-or-fetch the global registry row for this data+window, BEFORE any
    Qwen call or tuning.

    FRESH identity: the row must be unconsumed — a CONSUMED / REPLAY_ONLY /
    INVALIDATED row refuses the run with HELDOUT_ALREADY_CONSUMED, and a fresh
    row is atomically moved REGISTERED -> LOCKED_UNOPENED (locked_at recorded
    in this same pre-Qwen transaction).
    REPLAY identities: the row is ensured with status REPLAY_ONLY; replay
    evaluations never consume the fresh budget."""
    payload = heldout_identity_payload(track, snapshot["snapshot_sha256"], window, split.split_sha256)
    registry_id = heldout_registry_id(payload)
    row = _fetch_registry(conn, registry_id)
    if row is None:
        status = "REPLAY_ONLY" if track["eval_identity"] in REPLAY_IDENTITIES else "REGISTERED"
        conn.execute(
            f"""
            insert into {SCHEMA}.kg_qwen_heldout_registry
                (registry_id, global_sha256, global_identity, track_key, identity, snapshot_sha256,
                 eval_window, split_version, evaluator_version, status, registered_at)
            values (%s,%s,%s::jsonb,%s,%s,%s,%s::jsonb,%s,%s,%s,now())
            on conflict (registry_id) do nothing
            """,
            (
                registry_id,
                sha256_json(payload),
                json.dumps({"payload": payload, "seeded_from": "runtime"}, default=str),
                track["key"],
                track["eval_identity"],
                snapshot["snapshot_sha256"],
                json.dumps(payload["eval_window"], default=str),
                split.split_sha256,
                HELDOUT_EVALUATOR_VERSION,
                status,
            ),
        )
        row = _fetch_registry(conn, registry_id)
    assert row is not None  # insert-or-fetch above guarantees the row
    if track["eval_identity"] in REPLAY_IDENTITIES:
        return row
    if row["status"] != "REGISTERED":
        # CONSUMED / REPLAY_ONLY / INVALIDATED: this window can never be
        # opened as fresh held-out again — refuse BEFORE any tuning work.
        raise HiddenEvalConsumed(f"HELDOUT_ALREADY_CONSUMED:{registry_id}:{row['status']}")
    conn.execute(
        f"update {SCHEMA}.kg_qwen_heldout_registry set status='LOCKED_UNOPENED', locked_at=clock_timestamp() "
        f"where registry_id = %s and status = 'REGISTERED'",
        (registry_id,),
    )
    locked = _fetch_registry(conn, registry_id)
    return locked or row


def _consume_heldout_global(
    conn: Any,
    registry_id: str,
    run_id: str,
    first_qwen_started_at: Any,
    tuning_finished_at: Any,
) -> dict[str, Any]:
    """Atomically consume the global exactly-once fresh budget. The guarded
    UPDATE is the ONLY path that may mark a window CONSUMED; a second attempt
    (any run, any time) returns no row and raises HELDOUT_ALREADY_CONSUMED."""
    row = conn.execute(
        f"""
        update {SCHEMA}.kg_qwen_heldout_registry
        set status = 'CONSUMED', consumed_at = clock_timestamp(), consumer_run_id = %s,
            first_qwen_started_at = %s, tuning_finished_at = %s
        where registry_id = %s and status in ('REGISTERED', 'LOCKED_UNOPENED')
        returning consumed_at
        """,
        (run_id, first_qwen_started_at, tuning_finished_at, registry_id),
    ).fetchone()
    if not row:
        raise HiddenEvalConsumed(f"HELDOUT_ALREADY_CONSUMED:{registry_id}")
    consumed = _fetch_registry(conn, registry_id) or {}
    return {"consumed_at": _val(row, "consumed_at", 0), "registry": consumed}


def validate_time_chain(
    *,
    registered_at: Any,
    locked_at: Any,
    first_qwen_started_at: Any,
    tuning_finished_at: Any,
    consumed_at: Any,
    require_consumed: bool = True,
) -> dict[str, Any]:
    """Strict temporal ordering proof that the window was frozen before Qwen
    and consumed after tuning: registered <= locked < first_qwen <
    tuning_finished < consumed. Any violation blocks certification. Replay
    evaluations pass require_consumed=False (they never consume)."""
    stamps = {
        "registered_at": registered_at,
        "locked_at": locked_at,
        "first_qwen_started_at": first_qwen_started_at,
        "tuning_finished_at": tuning_finished_at,
        "consumed_at": consumed_at,
    }
    violations = [
        f"MISSING_TIMESTAMP:{k}" for k, v in stamps.items() if v is None and (k != "consumed_at" or require_consumed)
    ]
    if not violations:
        if registered_at > locked_at:
            violations.append("ORDER:registered_at>locked_at")
        if not locked_at < first_qwen_started_at:
            violations.append("ORDER:locked_at>=first_qwen_started_at")
        if not first_qwen_started_at < tuning_finished_at:
            violations.append("ORDER:first_qwen_started_at>=tuning_finished_at")
        if require_consumed and not tuning_finished_at < consumed_at:
            violations.append("ORDER:tuning_finished_at>=consumed_at")
    return {
        "ok": not violations,
        "violations": violations,
        "timestamps": {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in stamps.items()},
    }


def _pair_key(cell: Mapping[str, Any]) -> tuple[Any, Any, Any]:
    return (cell["origin_index"], cell["horizon"], cell["target_period"])


def _labeled_cells(cells: Sequence[Mapping[str, Any]]) -> dict[tuple[Any, Any, Any], Mapping[str, Any]]:
    return {_pair_key(c): c for c in cells if c.get("yhat") is not None and c.get("actual_value") is not None}


def _paired_baseline_comparison(
    rows: Sequence[TrainingRow],
    track_key: str,
    champion_adapter: str,
    champion_params: dict[str, Any],
    origin_indices: list[int],
    frequency: str,
    seed: int,
    champion_cells: list[dict[str, Any]],
) -> dict[str, Any]:
    """Strict same-sample comparison of the champion against every baseline.

    Both sides are restricted to cells with non-null prediction AND non-null
    label, keyed by (origin_index, horizon, target_period). If any baseline's
    key set differs from the champion's, the paired gate FAILS for that
    baseline (metrics on the intersection are still reported, marked
    NOT_CERTIFIABLE) — relative improvement is only certifiable on identical
    samples. The full paired CSV and its SHA-256 are embedded for audit."""
    champion = _labeled_cells(champion_cells)
    ordered_keys = sorted(champion, key=lambda k: (k[0], k[1]))
    baselines: dict[str, Any] = {}
    gate_failures: list[str] = []
    csv_models: list[tuple[str, str, dict[tuple[Any, Any, Any], Mapping[str, Any]]]] = [
        ("champion", champion_adapter, champion)
    ]
    for baseline_id in BASELINE_IDS:
        bcells = _labeled_cells(_champion_cells_payload(rows, baseline_id, {}, origin_indices, frequency, seed, True))
        matched = set(bcells) == set(champion)
        common = sorted(set(bcells) & set(champion), key=lambda k: (k[0], k[1]))
        bmetrics = _eval_metrics_payload([dict(bcells[k]) for k in common]) if common else None
        if not matched:
            gate_failures.append(
                f"PAIRED_SAMPLE_MISMATCH:{baseline_id}:champion_n={len(champion)}:baseline_n={len(bcells)}"
            )
        baselines[baseline_id] = {
            "adapter_id": baseline_id,
            "sample_matched": matched,
            "labeled_n": len(bcells),
            "paired_n": len(common),
            "wape": bmetrics["wape"] if bmetrics else None,
            "agreement_pct": bmetrics["agreement_pct"] if bmetrics else None,
            "coverage": bmetrics["coverage"] if bmetrics else None,
            "mae": bmetrics["mae"] if bmetrics else None,
            "rmse": bmetrics["rmse"] if bmetrics else None,
            "smape": bmetrics["smape"] if bmetrics else None,
            "mase": bmetrics["mase"] if bmetrics else None,
            "per_horizon_wape": {h: (bmetrics["per_horizon"].get(h) or {}).get("wape") for h in ("1", "2", "3", "4")}
            if bmetrics
            else None,
            "certifiable": matched,
        }
        if matched:
            csv_models.append((f"baseline:{baseline_id}", baseline_id, bcells))
    champion_metrics = _eval_metrics_payload([dict(champion[k]) for k in ordered_keys]) if ordered_keys else None
    matched_baselines = {k: v for k, v in baselines.items() if v["sample_matched"] and v["wape"] is not None}
    best_baseline_id = min(matched_baselines, key=lambda k: matched_baselines[k]["wape"]) if matched_baselines else None
    champion_wape = champion_metrics["wape"] if champion_metrics else None
    improvement = None
    if best_baseline_id is not None and champion_wape is not None:
        best_wape = float(matched_baselines[best_baseline_id]["wape"])
        improvement = (best_wape - champion_wape) / best_wape if best_wape > 0 else None

    buf = io.StringIO()
    buf.write("model_role,adapter_id,origin_index,horizon,target_period,yhat,actual_value\n")
    for role, adapter_id, cells in csv_models:
        for key in sorted(cells, key=lambda k: (k[0], k[1])):
            cell = cells[key]
            buf.write(f"{role},{adapter_id},{key[0]},{key[1]},{key[2]},{cell['yhat']},{cell['actual_value']}\n")
    csv_text = buf.getvalue()
    return {
        "track_key": track_key,
        "paired_n": len(ordered_keys),
        "champion_adapter_id": champion_adapter,
        "champion_wape_paired": champion_wape,
        "champion_agreement_pct_paired": champion_metrics["agreement_pct"] if champion_metrics else None,
        "champion_coverage_paired": champion_metrics["coverage"] if champion_metrics else None,
        "baselines": baselines,
        "best_baseline_id": best_baseline_id,
        "best_baseline_wape": (matched_baselines[best_baseline_id]["wape"] if best_baseline_id is not None else None),
        "relative_improvement_vs_best_baseline": improvement,
        "paired_gate_failures": gate_failures,
        "paired_sample_match": not gate_failures,
        "paired_csv_sha256": sha256_text(csv_text),
        "paired_csv": csv_text,
    }


def _fresh_certification(
    metrics: Mapping[str, Any], paired: Mapping[str, Any], time_chain: Mapping[str, Any]
) -> dict[str, Any]:
    """Fresh-held-out certification gates (Track B/C style identities only).
    Every gate must pass; failures are enumerated, never hidden."""
    wape = metrics.get("wape")
    agreement = metrics.get("agreement_pct")
    coverage = metrics.get("coverage")
    improvement = paired.get("relative_improvement_vs_best_baseline")
    gates = {
        "wape_max_0.30": wape is not None and float(wape) <= 0.30,
        "agreement_min_70": agreement is not None and float(agreement) >= 70.0,
        "coverage_min_0.85": coverage is not None and float(coverage) >= 0.85,
        "paired_baseline_improvement_min_0.01": improvement is not None and float(improvement) >= 0.01,
        "paired_sample_match": bool(paired.get("paired_sample_match")),
        "time_chain_ok": bool(time_chain.get("ok")),
        "single_consumption": True,  # reaching here means the atomic consume succeeded
    }
    failed = [k for k, ok in gates.items() if not ok]
    return {
        "gates": gates,
        "failed_gates": failed,
        "certified": not failed,
        "verdict": "FRESH_HELDOUT_CERTIFIED" if not failed else "FRESH_HELDOUT_GATES_FAILED",
        "target": {"wape_max": 0.30, "agreement_pct_min": 70.0, "coverage_min": 0.85, "baseline_improvement_min": 0.01},
    }


# ------------------------------------------------------- hidden eval lock/eval
def _lock_hidden_eval(
    conn: Any,
    track: dict[str, Any],
    snapshot: dict[str, Any],
    window: dict[str, Any],
    run_id: str,
    registry_id: str | None = None,
) -> str:
    """Lock the evaluation window BEFORE any Qwen call or any tuning.

    Content-addressed eval_id scoped to this run: each scheme freeze (run)
    gets exactly one evaluation of the window, enforced by the atomic
    eval_count 0->1 consume; the lock row itself is immutable evidence. The
    global exactly-once budget lives in kg_qwen_heldout_registry (registry_id
    link), which is what actually prevents cross-run re-opening."""
    eval_id = str(
        stable_uuid(
            "kq-eval-set",
            sha256_json(
                {
                    "track": track["key"],
                    "identity": track["eval_identity"],
                    "snapshot_sha256": snapshot["snapshot_sha256"],
                    "window": window,
                    "run_id": run_id,
                }
            ),
        )
    )
    existing = conn.execute(
        f"select eval_id::text from {SCHEMA}.kg_qwen_evaluation_sets where eval_id = %s", (eval_id,)
    ).fetchone()
    if not existing:
        conn.execute(
            f"""
            insert into {SCHEMA}.kg_qwen_evaluation_sets
                (eval_id, run_id, track, identity, snapshot_sha256, eval_window, locked_at, eval_count, registry_id)
            values (%s,%s,%s,%s,%s,%s::jsonb,now(),0,%s)
            on conflict do nothing
            """,
            (
                eval_id,
                run_id,
                track["key"],
                track["eval_identity"],
                snapshot["snapshot_sha256"],
                json.dumps(window),
                registry_id,
            ),
        )
    return eval_id


def _claim_eval_lock(conn: Any, eval_id: str, run_id: str) -> None:
    """Attach the pre-locked eval row to this run (only when unclaimed)."""
    conn.execute(
        f"update {SCHEMA}.kg_qwen_evaluation_sets set run_id = %s where eval_id = %s and run_id is null",
        (run_id, eval_id),
    )


def _consume_eval_once(conn: Any, eval_id: str, metrics: dict[str, Any]) -> dict[str, Any]:
    """Atomically consume the exactly-once evaluation budget (eval_count 0->1).

    The guarded UPDATE ... WHERE eval_count = 0 RETURNING is the only path
    that may write evaluation metrics; when it returns no row the window has
    already been evaluated and a second evaluation is refused."""
    row = conn.execute(
        f"""
        update {SCHEMA}.kg_qwen_evaluation_sets
        set eval_count = eval_count + 1, evaluated_at = now(), metrics = %s::jsonb,
            wape = %s, agreement_pct = %s, coverage = %s, mae = %s, rmse = %s, smape = %s, mase = %s
        where eval_id = %s and eval_count = 0
        returning eval_id::text, eval_count, evaluated_at
        """,
        (
            json.dumps(metrics, default=str),
            metrics["wape"],
            metrics["agreement_pct"],
            metrics["coverage"],
            metrics["mae"],
            metrics["rmse"],
            metrics["smape"],
            metrics["mase"],
            eval_id,
        ),
    ).fetchone()
    if not row:
        raise HiddenEvalConsumed(f"KQ_EVAL_ALREADY_CONSUMED:{eval_id}")
    return {
        "eval_id": _val(row, "eval_id", 0),
        "eval_count": int(_val(row, "eval_count", 1)),
        "evaluated_at": _val(row, "evaluated_at", 2),
    }


def _evaluate_heldout(
    conn: Any,
    run_id: str,
    track: dict[str, Any],
    snapshot: dict[str, Any],
    eval_id: str,
    champion_adapter: str,
    champion_params: dict[str, Any],
    seed: int,
    registry: dict[str, Any] | None = None,
    first_qwen_started_at: Any = None,
    tuning_finished_at: Any = None,
) -> dict[str, Any]:
    """Exactly-once held-out evaluation of the champion against FROZEN rows.

    Track A evaluates the reused test window (identity REUSED + NOT_FRESH);
    fresh tracks evaluate the locked hidden window. The fresh budget is
    consumed GLOBALLY via kg_qwen_heldout_registry (cross-run exactly-once);
    the per-run eval_count guard remains as a second layer. Champion and all
    baselines are compared on the strictly identical labeled sample."""
    rows: list[TrainingRow] = snapshot["rows"]
    frequency = track["frequency"]
    n_eval = track["test_n"] or track["hidden_n"]
    # rolling origins whose H1-H4 labels land inside the eval window
    last = len(rows) - 1
    offsets = list(range(n_eval - max(HORIZONS), 0, -4))[:12]
    origin_indices = [idx for idx in (last - off for off in offsets) if idx >= 59 and idx + max(HORIZONS) <= last]
    cells = _champion_cells_payload(rows, champion_adapter, champion_params, origin_indices, frequency, seed, True)
    metrics = _eval_metrics_payload(cells)
    paired = _paired_baseline_comparison(
        rows, track["key"], champion_adapter, champion_params, origin_indices, frequency, seed, cells
    )
    is_replay = track["eval_identity"] in REPLAY_IDENTITIES
    consumed_at = None
    registry_after: dict[str, Any] | None = registry
    if registry is not None and not is_replay:
        consumed = _consume_heldout_global(
            conn, str(registry["registry_id"]), run_id, first_qwen_started_at, tuning_finished_at
        )
        consumed_at = consumed["consumed_at"]
        registry_after = consumed.get("registry") or registry
    time_chain = None
    certification = None
    if registry is not None:
        time_chain = validate_time_chain(
            registered_at=registry_after.get("registered_at") if registry_after else None,
            locked_at=registry_after.get("locked_at") if registry_after else None,
            first_qwen_started_at=(registry_after.get("first_qwen_started_at") if registry_after else None)
            or first_qwen_started_at,
            tuning_finished_at=(registry_after.get("tuning_finished_at") if registry_after else None)
            or tuning_finished_at,
            consumed_at=consumed_at,
            require_consumed=not is_replay,
        )
        if not is_replay:
            certification = _fresh_certification(metrics, paired, time_chain)
    metrics.update(
        {
            "identity": track["eval_identity"],
            "flags": list(track["eval_flags"]),
            "kind": EVAL_KIND_HIDDEN if track["hidden_n"] else EVAL_KIND_REUSED,
            "adapter_id": champion_adapter,
            "params": champion_params,
            "n_origins": len(origin_indices),
            "window": track_eval_window_payload(track["key"], [r.period_start for r in rows]),
            "registry_id": str(registry["registry_id"]) if registry else None,
            "registry_status": (registry_after or {}).get("status") if registry else None,
            "evaluator_version": HELDOUT_EVALUATOR_VERSION,
            "classification": (
                "REPLAY_ONLY_NOT_FRESH" if is_replay else ("FRESH_CONSUMED_ONCE" if consumed_at else "FRESH")
            ),
            "classification_note_zh": (
                "历史重复评估结果，不构成独立盲测认证。"
                if is_replay
                else "新鲜独立留存：全局注册表原子消费一次，调参与 Qwen 调用之前锁定。"
            ),
            "paired": paired,
            "time_chain": time_chain,
            "certification": certification,
        }
    )
    consumed = _consume_eval_once(conn, eval_id, metrics)
    return {**metrics, **consumed}


# ------------------------------------------------------------- run persistence
def _find_run_by_key(conn: Any, idempotency_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        f"select run_id::text, track_key, status, stop_reason, target_status, snapshot_sha256, "
        f"split_sha256, seed from {SCHEMA}.kg_qwen_agent_runs where idempotency_key = %s",
        (idempotency_key,),
    ).fetchone()
    if not row:
        return None
    return {
        "run_id": _val(row, "run_id", 0),
        "track_key": _val(row, "track_key", 1),
        "status": _val(row, "status", 2),
        "stop_reason": _val(row, "stop_reason", 3),
        "target_status": _val(row, "target_status", 4),
        "snapshot_sha256": _val(row, "snapshot_sha256", 5),
        "split_sha256": _val(row, "split_sha256", 6),
        "seed": _val(row, "seed", 7),
    }


def _replay_summary(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "IDEMPOTENT_REPLAY",
        "run_id": run["run_id"],
        "track_key": run["track_key"],
        "run_status": run["status"],
        "stop_reason": run["stop_reason"],
        "target_status": run["target_status"],
        "snapshot_sha256": run["snapshot_sha256"],
        "split_sha256": run["split_sha256"],
        "limitations": LIMITATIONS,
    }


def _insert_agent_run(
    conn: Any,
    idempotency_key: str,
    track: dict[str, Any],
    snapshot: dict[str, Any],
    split: TemporalSplit,
    window: dict[str, Any],
    constants: dict[str, Any],
    prompt_template_sha256: str | None,
    qwen_fingerprint: str | None,
) -> str:
    run_id = str(stable_uuid("kq-agent-run", idempotency_key))
    plan_validator_version = None
    try:
        plan_validator_version = _kq_module("plan_contract").PLAN_VALIDATOR_VERSION
    except Exception:
        plan_validator_version = None
    conn.execute(
        f"""
        insert into {SCHEMA}.kg_qwen_agent_runs
            (run_id, idempotency_key, track_key, status, qwen_model_id, qwen_model_fingerprint,
             qwen_runtime, prompt_template_sha256, snapshot_id, snapshot_sha256, split_sha256,
             eval_window, budgets, seed, code_version, plan_validator_version)
        values (%s,%s,%s,'RUNNING',%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s)
        on conflict do nothing
        """,
        (
            run_id,
            idempotency_key,
            track["key"],
            constants["QWEN_MODEL_ID"],
            qwen_fingerprint,
            constants["QWEN_RUNTIME_LABEL"],
            prompt_template_sha256,
            snapshot["snapshot_id"],
            snapshot["snapshot_sha256"],
            split.split_sha256,
            json.dumps(window, default=str),
            json.dumps(budgets_payload(constants), default=str),
            constants["KQ_SEED"],
            constants["KQ_IMPL_VERSION"],
            plan_validator_version,
        ),
    )
    authoritative = _find_run_by_key(conn, idempotency_key)
    return str(authoritative["run_id"]) if authoritative else run_id


def _persist_plan(conn: Any, payload: dict[str, Any]) -> str:
    plan_id = str(stable_uuid("kq-plan", payload["plan_sha256"] + f":{payload['run_id']}:{payload['round_index']}"))
    conn.execute(
        f"""
        insert into {SCHEMA}.kg_qwen_tuning_plans
            (plan_id, run_id, round_index, plan_source, plan_json, plan_sha256, prompt_sha256,
             response_sha256, qwen_latency_ms, validation_status, validation_errors)
        values (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s::jsonb)
        on conflict do nothing
        """,
        (
            plan_id,
            payload["run_id"],
            payload["round_index"],
            payload["plan_source"],
            json.dumps(payload["plan_json"], default=str),
            payload["plan_sha256"],
            payload["prompt_sha256"],
            payload["response_sha256"],
            payload["qwen_latency_ms"],
            payload["validation_status"],
            json.dumps(payload["validation_errors"], default=str),
        ),
    )
    return plan_id


def _persist_round(conn: Any, payload: dict[str, Any]) -> str:
    round_id = str(stable_uuid("kq-round", f"{payload['run_id']}:{payload['round_index']}"))
    conn.execute(
        f"""
        insert into {SCHEMA}.kg_qwen_tuning_rounds
            (round_id, run_id, round_index, status, trials_executed, best_adapter_id, best_trial_id,
             best_val_wape, best_val_agreement_pct, best_val_coverage, baseline_best_wape,
             improvement_vs_baseline, target_met, stop_reason, finished_at)
        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
        on conflict do nothing
        """,
        (
            round_id,
            payload["run_id"],
            payload["round_index"],
            payload["status"],
            payload["trials_executed"],
            payload["best_adapter_id"],
            payload["best_trial_id"],
            payload["best_val_wape"],
            payload["best_val_agreement_pct"],
            payload["best_val_coverage"],
            payload["baseline_best_wape"],
            payload["improvement_vs_baseline"],
            payload["target_met"],
            payload["stop_reason"],
        ),
    )
    return round_id


def _persist_trial(conn: Any, payload: dict[str, Any]) -> str:
    trial_id = str(stable_uuid("kq-trial", f"{payload['run_id']}:{payload['trial_sha256']}"))
    conn.execute(
        f"""
        insert into {SCHEMA}.kg_qwen_trials
            (trial_id, run_id, round_index, trial_index, adapter_id, params, params_sha256,
             parameter_source, origin_sha256s, per_horizon_metrics, common_wape, agreement_pct,
             coverage, avg_interval_width, complexity_penalty, objective_value, gates, gate_pass,
             failure_reason, artifact_sha256, dataset_sha256, trial_sha256, elapsed_ms)
        values (%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s,
                %s::jsonb,%s,%s,%s,%s,%s,%s)
        on conflict do nothing
        """,
        (
            trial_id,
            payload["run_id"],
            payload["round_index"],
            payload["trial_index"],
            payload["adapter_id"],
            json.dumps(payload["params"], default=str),
            payload["params_sha256"],
            json.dumps(payload["parameter_source"], default=str),
            json.dumps(payload["origin_sha256s"], default=str),
            json.dumps(payload["per_horizon_metrics"], default=str),
            payload["common_wape"],
            payload["agreement_pct"],
            payload["coverage"],
            payload["avg_interval_width"],
            payload["complexity_penalty"],
            payload["objective_value"],
            json.dumps(payload["gates"], default=str),
            payload["gate_pass"],
            payload["failure_reason"],
            payload["artifact_sha256"],
            payload["dataset_sha256"],
            payload["trial_sha256"],
            payload["elapsed_ms"],
        ),
    )
    return trial_id


def _persist_round_bundle(conn: Any, run_id: str, round_result: Any) -> None:
    """on_round_complete checkpoint callback: plan + trials + round, all
    INSERT-only with content-addressed ids (replays converge)."""
    _persist_plan(conn, plan_row_payload(run_id, round_result.round_index, round_result))
    best_trial_id: str | None = None
    trial_list = list(round_result.trials or [])
    adapter_ids = list(getattr(round_result, "trial_adapter_ids", None) or [])
    for position, trial in enumerate(trial_list):
        adapter_id = (
            adapter_ids[position]
            if position < len(adapter_ids)
            else getattr(trial, "adapter_id", None) or round_result.best_adapter_id or "unknown"
        )
        payload = trial_row_payload(run_id, round_result.round_index, adapter_id, trial)
        trial_id = _persist_trial(conn, payload)
        if round_result.best_trial_sha256 and payload["trial_sha256"] == round_result.best_trial_sha256:
            best_trial_id = trial_id
    _persist_round(conn, round_row_payload(run_id, round_result, best_trial_id))


# ------------------------------------------------------------------ planner
def _build_planner_and_context(
    track: dict[str, Any],
    trainval_rows: Sequence[TrainingRow],
    split: TemporalSplit,
    snapshot_sha256: str,
) -> tuple[Any, Any, str | None, str | None]:
    """Construct the Qwen planner callable and the ToolContext.

    The planner proposes one QwenProposal per (context, round_index): it asks
    Qwen via qwen_client, parses/validates with plan_contract (<=2 retries with
    error feedback) and falls back to the deterministic plan when Qwen is
    unavailable or keeps failing. Returns (planner, tool_context,
    prompt_template_sha256, qwen_fingerprint)."""
    agent_tools = _kq_module("agent_tools")
    plan_contract = _kq_module("plan_contract")
    qwen_client = _kq_module("qwen_client")
    loop_mod = _kq_module("loop")

    tool_context = agent_tools.ToolContext(
        track_key=track["key"],
        frequency=track["frequency"],
        trainval_rows=list(trainval_rows),
        snapshot_sha256=snapshot_sha256,
        split=split,
        validation_summaries=[],
    )
    prompt_sha = getattr(qwen_client, "PROMPT_TEMPLATE_SHA256", None)
    fingerprint = None
    client = None
    try:
        client = qwen_client.QwenClient()
        fingerprint = qwen_client.model_fingerprint(client.model_dir) if hasattr(client, "model_dir") else None
    except Exception:
        client = None

    def planner(context: dict[str, Any], round_index: int) -> Any:
        prior_errors: list[str] = []
        nonlocal client
        for _attempt in range(3):  # initial + <=2 retries with error feedback
            if client is None:
                try:  # rebuild a dead/unavailable client instead of disabling Qwen for the run
                    client = qwen_client.QwenClient()
                except Exception:
                    break
            try:
                messages = qwen_client.build_messages(context, round_index, prior_errors or None)
                response = client.complete(messages)
                raw_plan = plan_contract.parse_plan_json(response.text)
                plan = plan_contract.normalize_compact_plan(
                    raw_plan, snapshot_sha256=snapshot_sha256, frequency=track["frequency"]
                )
                errors = plan_contract.validate_plan(
                    plan, expected_snapshot_sha256=snapshot_sha256, frequency=track["frequency"]
                )
                if not errors:
                    return loop_mod.QwenProposal(
                        plan=plan,
                        plan_source="QWEN",
                        prompt_sha256=response.prompt_sha256,
                        response_sha256=response.response_sha256,
                        latency_ms=response.latency_ms,
                    )
                prior_errors = [f"{e.code}: {e.message}" for e in errors]
            except qwen_client.QwenUnavailable as exc:
                prior_errors = [str(exc)]
                with contextlib.suppress(Exception):
                    client.close()
                client = None  # force a fresh worker on the next attempt
            except Exception as exc:  # parse/transport failure -> retry with feedback
                prior_errors = [str(exc)]
        try:
            if client is not None:
                client.close()
        except Exception:
            pass
        reason = "; ".join(prior_errors) if prior_errors else "QWEN_UNAVAILABLE"
        plan = plan_contract.deterministic_fallback_plan(snapshot_sha256, track["frequency"], reason)
        return loop_mod.QwenProposal(
            plan=plan,
            plan_source="QWEN_FALLBACK_DETERMINISTIC",
            prompt_sha256=prompt_sha,
            response_sha256=None,
            latency_ms=None,
        )

    return planner, tool_context, prompt_sha, fingerprint


# ------------------------------------------------------------------- shadow
def _insert_shadow_run(
    conn: Any,
    run_id: str,
    idempotency_key: str,
    track: dict[str, Any],
    snapshot: dict[str, Any],
    split: TemporalSplit,
    champion_adapter: str,
) -> str:
    """Insert the Shadow run row; returns the authoritative shadow run id.

    run_key is scoped by the idempotency key: every autotuning agent run gets
    its own Shadow run (append-only lineage; older runs are never reused or
    overwritten, and kg_learning_cycles' unique(run_id, cycle_type) then
    applies per agent run)."""
    run_key = f"kg-qwen:{track['key']}:{snapshot['snapshot_sha256'][:12]}:{idempotency_key}"
    profile = {
        "kg_qwen": True,
        "track_key": track["key"],
        "snapshot_sha256": snapshot["snapshot_sha256"],
        "dataset_sha256": snapshot["dataset_sha256"],
        "split_sha256": split.split_sha256,
        "recommendation_id": DEMO_RECOMMENDATION_ID,
        "champion_adapter": champion_adapter,
        "impl_version": _kq_constants()["KQ_IMPL_VERSION"],
        "note": "champion of record lives in kg_champion_decisions; Shadow only",
    }
    conn.execute(
        f"""
        insert into {SCHEMA}.kg_forecast_shadow_runs
            (run_id, run_key, idempotency_key, mode, status, disease_slug, country_alpha3,
             metric_code, cadence, data_through, model_name, fallback_reason,
             promotion_eligible, active_pointer_changed, trusted_rows_changed,
             profile, selection_trace)
        values (%s,%s,%s,'SHADOW','COMPLETED',%s,%s,%s,%s,%s,%s,%s,false,false,0,%s::jsonb,%s::jsonb)
        on conflict do nothing
        """,
        (
            run_id,
            run_key,
            f"kg-qwen:{idempotency_key}",
            snapshot["disease"],
            snapshot["country"],
            snapshot["indicator"],
            snapshot["frequency"],
            snapshot["rows"][-1].period_start,
            champion_adapter,
            None,
            json.dumps(profile, default=str),
            json.dumps({"shadow_notice": "SHADOW_ONLY_NO_PROMOTION", "replay": "DEMO_HISTORICAL_REPLAY"}),
        ),
    )
    existing = conn.execute(
        f"select run_id::text from {SCHEMA}.kg_forecast_shadow_runs where run_key = %s", (run_key,)
    ).fetchone()
    return str(_val(existing, "run_id", 0)) if existing else run_id


def _persist_shadow_values(
    conn: Any,
    shadow_run_id: str,
    track: dict[str, Any],
    snapshot: dict[str, Any],
    champion_adapter: str,
    champion_params: dict[str, Any],
    seed: int,
) -> dict[str, int]:
    """Replay-window predictions (actual=NULL, AWAITING_ACTUAL) + future H1-H4."""
    rows: list[TrainingRow] = snapshot["rows"]
    frequency = track["frequency"]
    n_eval = track["test_n"] or track["hidden_n"]
    last = len(rows) - 1
    offsets = list(range(n_eval - max(HORIZONS), 0, -4))[:12]
    replay_origins = [idx for idx in (last - off for off in offsets) if idx >= 59 and idx + max(HORIZONS) <= last]
    replay_cells = _champion_cells_payload(
        rows, champion_adapter, champion_params, replay_origins, frequency, seed, False
    )
    replay_count = len(
        pt_pipeline._persist_test_shadow_values(conn, shadow_run_id, replay_cells, snapshot["snapshot_sha256"])
    )
    future_count = pt_pipeline._persist_future(
        conn, shadow_run_id, champion_adapter, rows, champion_params, frequency, seed, snapshot["dataset_sha256"]
    )
    return {"replay_awaiting_written": replay_count, "future_awaiting": future_count}


def _mature_replay_feedback(
    conn: Any,
    shadow_run_id: str,
    adapter_id: str,
    rows: Sequence[TrainingRow],
    track: dict[str, Any],
    champion_params: dict[str, Any],
    seed: int,
) -> dict[str, int]:
    """Mature the AWAITING replay feedback rows whose labels exist in the
    FROZEN snapshot (DEMO_HISTORICAL_REPLAY; supersede + append, never rewrite)."""
    frequency = track["frequency"]
    n_eval = track["test_n"] or track["hidden_n"]
    last = len(rows) - 1
    offsets = list(range(n_eval - max(HORIZONS), 0, -4))[:12]
    replay_origins = [idx for idx in (last - off for off in offsets) if idx >= 59 and idx + max(HORIZONS) <= last]
    cells = _champion_cells_payload(rows, adapter_id, champion_params, replay_origins, frequency, seed, False)
    frozen = list(rows)
    return pt_pipeline._mature_test_feedback(conn, shadow_run_id, adapter_id, cells, frozen)


def _feedback_ledger(conn: Any, shadow_run_id: str) -> dict[str, Any]:
    """Aggregate the objective-feedback ledger for one shadow run (read-only)."""
    rows = conn.execute(
        f"""
        select feedback_maturity, count(*)::int as n,
               avg(absolute_error)::float as mean_abs_error,
               avg(case when interval_hit then 1.0 else 0.0 end)
                 filter (where interval_hit is not null)::float as empirical_coverage
        from {SCHEMA}.kg_forecast_objective_feedback
        where run_id = %s
        group by feedback_maturity
        """,
        (shadow_run_id,),
    ).fetchall()
    by_maturity = {_val(r, "feedback_maturity", 0): r for r in rows}

    def _g(key: str, field: str, idx: int) -> Any:
        row = by_maturity.get(key)
        return _val(row, field, idx) if row else None

    return {
        "matured_count": _g(MATURITY_MATURED, "n", 1) or 0,
        "awaiting_count": _g(MATURITY_AWAITING, "n", 1) or 0,
        "superseded_count": _g(MATURITY_SUPERSEDED, "n", 1) or 0,
        "mean_abs_error": _g(MATURITY_MATURED, "mean_abs_error", 2),
        "empirical_coverage": _g(MATURITY_MATURED, "empirical_coverage", 3),
    }


def _champion_source_db(loop_source: str) -> str:
    """Map loop champion sources onto the 0040-era CHECK constraint domain.

    kg_champion_decisions.champion_source only allows KG_RECOMMENDED_MODEL or
    EMPIRICAL_BASELINE_OVERRIDE; the loop's TUNED_MODEL/BASELINE are mapped
    here at the persistence boundary (the loop itself stays agnostic).
    """
    return "KG_RECOMMENDED_MODEL" if loop_source == "TUNED_MODEL" else "EMPIRICAL_BASELINE_OVERRIDE"


def _latest_champion_source(conn: Any, shadow_run_id: str) -> str | None:
    """The incumbent champion_source of the newest prior decision for one run."""
    row = conn.execute(
        f"select champion_source from {SCHEMA}.kg_champion_decisions where run_id = %s "
        f"order by created_at desc limit 1",
        (shadow_run_id,),
    ).fetchone()
    return str(_val(row, "champion_source", 0)) if row else None


def _persist_learning_cycle(
    conn: Any,
    shadow_run_id: str,
    cycle_type: str,
    champion: dict[str, Any],
    evidence: dict[str, Any],
    min_matured: int,
    matured_count: int,
) -> str:
    esha = sha256_json(evidence)
    # kg_learning_cycles enforces unique(run_id, cycle_type): when a cycle of
    # this type already exists for the shadow run (e.g. a replayed reconcile
    # with a different idempotency key), reuse it so the champion decision
    # converges onto the existing cycle instead of violating the FK.
    existing = conn.execute(
        f"select learning_cycle_id::text from {SCHEMA}.kg_learning_cycles where run_id = %s and cycle_type = %s",
        (shadow_run_id, cycle_type),
    ).fetchone()
    if existing:
        return str(_val(existing, "learning_cycle_id", 0))
    cycle_id = str(stable_uuid("learning-cycle", f"{shadow_run_id}:{cycle_type}:{esha[:16]}"))
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
            shadow_run_id,
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
            shadow_run_id,
            champion["champion_adapter_id"],
            champion["champion_source"],
            champion["kg_model_status"],
            champion["champion_reason"],
        ),
    )
    return cycle_id


# -------------------------------------------------------------------- driver
def execute_autotuning(conn: Any, *, actor: str, idempotency_key: str, track_key: str) -> dict[str, Any]:
    """Run the full Qwen autotuning loop for one track. Idempotent: a run with
    the same idempotency key returns IDEMPOTENT_REPLAY and writes nothing."""
    track = _track(track_key)
    constants = _kq_constants()
    seed = int(constants["KQ_SEED"])

    existing = _find_run_by_key(conn, idempotency_key)
    if existing is not None:
        return _replay_summary(existing)

    # 1. freeze snapshot (Track B creates the FIN snapshot on first run)
    snapshot = _ensure_track_snapshot(conn, track)
    rows: list[TrainingRow] = snapshot["rows"]
    if track["hidden_n"] and len(rows) < track["tuning_n"] + track["hidden_n"]:
        raise ValueError(f"KQ_TRACK_B_INFEASIBLE: need {track['tuning_n'] + track['hidden_n']} rows, got {len(rows)}")

    # 2. deterministic run id + eval window payload (the lock itself is
    # written at step 4, right after the run row exists, still before any
    # Qwen generation).
    run_id = str(stable_uuid("kq-agent-run", idempotency_key))
    window = track_eval_window_payload(track_key, [r.period_start for r in rows])

    # 3. split + contracts (additive upserts)
    split = _ensure_track_split(conn, track, snapshot)
    _ensure_contracts(conn, track["frequency"])

    # 4. GLOBAL held-out registration + lock FIRST — a consumed/replay-only
    # window refuses the run here, before any planner context is built, any
    # Qwen call is made, or any trial runs. Then the agent run row and the
    # per-run eval lock row (both still strictly before any Qwen generation).
    registry = _register_heldout_window(conn, track, snapshot, window, split)
    planner, tool_context, prompt_sha, fingerprint = _build_planner_and_context(
        track, rows[: track["train_n"] + track["val_n"]], split, snapshot["snapshot_sha256"]
    )
    run_id = _insert_agent_run(
        conn, idempotency_key, track, snapshot, split, window, constants, prompt_sha, fingerprint
    )
    eval_lock_id = _lock_hidden_eval(conn, track, snapshot, window, run_id, str(registry["registry_id"]))
    _claim_eval_lock(conn, eval_lock_id, run_id)
    # GOVERNANCE: the freeze (immutable snapshot + split + global registry
    # REGISTERED->LOCKED_UNOPENED + run row + per-run eval lock) commits here,
    # in its OWN transaction, strictly before any Qwen call or tuning. A crash
    # during tuning can no longer roll the lock back: the window stays locked
    # (LOCKED_UNOPENED) with a committed locked_at that precedes every later
    # timestamp in the time chain. Idempotency stays intact because the
    # committed run row makes any concurrent same-key request replay via
    # _find_run_by_key once the advisory xact lock is released by this commit.
    conn.commit()

    # 5. multi-round autotuning loop (checkpoint persistence per round); the
    # first planner invocation timestamp is the time-chain anchor proving the
    # window was locked before Qwen/tuning touched anything.
    loop_mod = _kq_module("loop")
    timing: dict[str, Any] = {}
    base_planner = planner

    def timed_planner(context: dict[str, Any], round_index: int) -> Any:
        if "first_qwen_started_at" not in timing:
            timing["first_qwen_started_at"] = datetime.now(timezone.utc)
        return base_planner(context, round_index)

    def on_round_complete(round_result: Any) -> None:
        _persist_round_bundle(conn, run_id, round_result)

    tuning_rows = rows[: track["tuning_n"]] if track["hidden_n"] else rows[: track["train_n"] + track["val_n"]]
    loop_result = loop_mod.run_autotuning_loop(
        track_key=track["key"],
        frequency=track["frequency"],
        trainval_rows=tuning_rows,
        split=split,
        snapshot_sha256=snapshot["snapshot_sha256"],
        planner=timed_planner,
        tool_context=tool_context,
        on_round_complete=on_round_complete,
        seed=seed,
        max_rounds=int(constants["MAX_ROUNDS"]),
        max_trials_per_round=int(constants["MAX_TRIALS_PER_ROUND"]),
        max_total_seconds=int(constants["MAX_TOTAL_MINUTES"]) * 60,
    )
    tuning_finished_at = datetime.now(timezone.utc)
    first_qwen_started_at = timing.get("first_qwen_started_at") or tuning_finished_at
    conn.execute(
        f"update {SCHEMA}.kg_qwen_agent_runs set first_qwen_started_at=%s, tuning_finished_at=%s where run_id=%s",
        (first_qwen_started_at, tuning_finished_at, run_id),
    )

    # 6. champion -> Shadow run -> exactly-once held-out evaluation
    champion_adapter = loop_result.champion_adapter_id
    champion_params = dict(loop_result.champion_params or {})
    shadow_run_id = _insert_shadow_run(
        conn,
        str(stable_uuid("kq-shadow-run", f"kg-qwen-shadow:{idempotency_key}")),
        idempotency_key,
        track,
        snapshot,
        split,
        champion_adapter,
    )
    evaluation = _evaluate_heldout(
        conn,
        run_id,
        track,
        snapshot,
        eval_lock_id,
        champion_adapter,
        champion_params,
        seed,
        registry=registry,
        first_qwen_started_at=first_qwen_started_at,
        tuning_finished_at=tuning_finished_at,
    )

    # 7. Shadow publication: replay window (actual=NULL) + future H1-H4
    feedback = _persist_shadow_values(conn, shadow_run_id, track, snapshot, champion_adapter, champion_params, seed)

    # 8. DEMO_HISTORICAL_REPLAY maturation -> learning cycle + champion decision
    feedback.update(_mature_replay_feedback(conn, shadow_run_id, champion_adapter, rows, track, champion_params, seed))
    ledger = _feedback_ledger(conn, shadow_run_id)
    target_met = bool(loop_result.target_met)
    target_status = TARGET_STATUS_MET if target_met else TARGET_STATUS_NOT_MET
    champion = {
        "champion_adapter_id": champion_adapter,
        "champion_source": _champion_source_db(loop_result.champion_source),
        "kg_model_status": (
            "EXECUTED_CHAMPION" if loop_result.champion_source == "TUNED_MODEL" else "EXECUTED_NOT_CHAMPION"
        ),
        "champion_reason": "TARGET_MET" if target_met else loop_result.stop_reason,
    }
    evidence = {
        "run_id": shadow_run_id,
        "agent_run_id": run_id,
        "track_key": track["key"],
        "recommendation_id": DEMO_RECOMMENDATION_ID,
        "replay": "DEMO_HISTORICAL_REPLAY",
        "eval_identity": track["eval_identity"],
        "eval_flags": list(track["eval_flags"]),
        "best_val_wape": loop_result.best_val_wape,
        "best_val_agreement_pct": loop_result.best_val_agreement_pct,
        "best_val_coverage": loop_result.best_val_coverage,
        "baseline_metrics": loop_result.baseline_metrics,
        "champion_params": champion_params,
        "heldout": {k: evaluation.get(k) for k in ("wape", "agreement_pct", "coverage", "identity", "eval_count")},
        "feedback": feedback,
        "ledger": ledger,
        "learning_rule": "CONTROLLED_BATCH_LEARNING",
        "limitations": LIMITATIONS,
    }
    cycle_id = _persist_learning_cycle(
        conn, shadow_run_id, CYCLE_TYPE_EVALUATION, champion, evidence, 1, feedback.get("test_matured", 0)
    )

    # 9. target status back onto the agent run (lifecycle columns only)
    conn.execute(
        f"update {SCHEMA}.kg_qwen_agent_runs set status='COMPLETED', stop_reason=%s, target_status=%s, "
        f"finished_at=now() where run_id=%s",
        (loop_result.stop_reason, target_status, run_id),
    )

    return {
        "status": "COMPLETED",
        "track_key": track["key"],
        "idempotency_key": idempotency_key,
        "run_id": run_id,
        "shadow_run_id": shadow_run_id,
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_sha256": snapshot["snapshot_sha256"],
        "split_sha256": split.split_sha256,
        "eval_id": evaluation["eval_id"],
        "eval_identity": track["eval_identity"],
        "eval_flags": list(track["eval_flags"]),
        "rounds": len(loop_result.rounds),
        "champion_adapter_id": champion_adapter,
        "champion_source": loop_result.champion_source,
        "champion_params": champion_params,
        "best_val_wape": loop_result.best_val_wape,
        "best_val_agreement_pct": loop_result.best_val_agreement_pct,
        "best_val_coverage": loop_result.best_val_coverage,
        "heldout_wape": evaluation["wape"],
        "heldout_agreement_pct": evaluation["agreement_pct"],
        "registry_id": str(registry["registry_id"]),
        "registry_status": evaluation.get("registry_status"),
        "classification": evaluation.get("classification"),
        "time_chain": evaluation.get("time_chain"),
        "certification": evaluation.get("certification"),
        "paired_summary": {
            "paired_n": (evaluation.get("paired") or {}).get("paired_n"),
            "paired_sample_match": (evaluation.get("paired") or {}).get("paired_sample_match"),
            "best_baseline_id": (evaluation.get("paired") or {}).get("best_baseline_id"),
            "best_baseline_wape": (evaluation.get("paired") or {}).get("best_baseline_wape"),
            "relative_improvement_vs_best_baseline": (evaluation.get("paired") or {}).get(
                "relative_improvement_vs_best_baseline"
            ),
            "paired_csv_sha256": (evaluation.get("paired") or {}).get("paired_csv_sha256"),
        },
        "target_met": target_met,
        "target_status": target_status,
        "stop_reason": loop_result.stop_reason,
        "feedback": feedback,
        "ledger": ledger,
        "learning_cycle_ids": [cycle_id],
        "limitations": LIMITATIONS,
    }


def _shadow_run_for_agent(conn: Any, agent_idempotency_key: str, track_key: str, snapshot_sha256: str) -> Any:
    """Locate THIS agent run's Shadow run: exact match on the scoped shadow
    idempotency key first, then the newest shadow run for track+snapshot."""
    row = conn.execute(
        f"select run_id::text, model_name from {SCHEMA}.kg_forecast_shadow_runs "
        f"where idempotency_key = %s order by created_at desc limit 1",
        (f"kg-qwen:{agent_idempotency_key}",),
    ).fetchone()
    if row:
        return row
    return conn.execute(
        f"select run_id::text, model_name from {SCHEMA}.kg_forecast_shadow_runs "
        f"where profile->>'track_key' = %s and profile->>'snapshot_sha256' = %s "
        f"order by created_at desc limit 1",
        (track_key, snapshot_sha256),
    ).fetchone()


# ------------------------------------------------------------------ reconcile
def reconcile_feedback(conn: Any, *, actor: str, idempotency_key: str, run_id: str) -> dict[str, Any]:
    """Mature AWAITING_ACTUAL feedback whose actuals already exist in the
    FROZEN snapshot, aggregate the performance ledger, evaluate retrain
    triggers and append a new learning cycle. Never overwrites old rows."""
    run = conn.execute(
        f"select run_id::text, track_key, snapshot_sha256, snapshot_id::text, status, idempotency_key "
        f"from {SCHEMA}.kg_qwen_agent_runs where run_id = %s",
        (run_id,),
    ).fetchone()
    if not run:
        raise ValueError(f"KQ_RUN_NOT_FOUND:{run_id}")
    track = _track(str(_val(run, "track_key", 1)))
    snapshot_id = _val(run, "snapshot_id", 3)
    snapshot_sha = _val(run, "snapshot_sha256", 2)

    shadow = _shadow_run_for_agent(conn, str(_val(run, "idempotency_key", 5)), track["key"], str(snapshot_sha))
    if not shadow:
        raise ValueError(f"KQ_SHADOW_RUN_NOT_FOUND:{run_id}")
    shadow_run_id = str(_val(shadow, "run_id", 0))
    champion_adapter = str(_val(shadow, "model_name", 1))

    frozen = pt_pipeline._load_frozen_rows(conn, snapshot_id)
    matured = _mature_replay_feedback(conn, shadow_run_id, champion_adapter, frozen, track, {}, KQ_SEED_DEFAULT)
    ledger = _feedback_ledger(conn, shadow_run_id)

    # retrain triggers: matured volume / performance degradation proxy /
    # revision changes / drift (drift not computed in the demo replay)
    revision_max = max(r.revision for r in frozen) if frozen else 1
    triggers = {
        "matured_feedback": ledger["matured_count"] >= RETRAIN_MIN_MATURED,
        "performance_drop": bool(
            ledger["mean_abs_error"] is not None
            and ledger["empirical_coverage"] is not None
            and ledger["empirical_coverage"] < 0.5
        ),
        "revision_changed": revision_max > 1,
        "drift": False,
        "drift_assessment": "NOT_COMPUTED_DEMO_REPLAY",
    }
    triggered = any(v is True for k, v in triggers.items() if k != "drift_assessment")
    incumbent = _latest_champion_source(conn, shadow_run_id)
    champion = {
        "champion_adapter_id": champion_adapter,
        "champion_source": incumbent or _champion_source_db("BASELINE"),
        "kg_model_status": "EXECUTED_NOT_CHAMPION",
        "champion_reason": "RETRAIN_TRIGGERED" if triggered else "INCUMBENT_RETAINED_NO_TRIGGER",
    }
    evidence = {
        "run_id": shadow_run_id,
        "agent_run_id": run_id,
        "track_key": track["key"],
        "actor": actor,
        "idempotency_key": idempotency_key,
        "replay": "DEMO_HISTORICAL_REPLAY",
        "newly_matured": matured,
        "ledger": ledger,
        "triggers": triggers,
        "retrain_triggered": triggered,
        "learning_rule": "CONTROLLED_BATCH_LEARNING",
        "limitations": LIMITATIONS,
    }
    cycle_id = _persist_learning_cycle(
        conn,
        shadow_run_id,
        CYCLE_TYPE_RECONCILE,
        champion,
        evidence,
        RETRAIN_MIN_MATURED,
        ledger["matured_count"],
    )
    return {
        "status": "COMPLETED",
        "run_id": run_id,
        "shadow_run_id": shadow_run_id,
        "newly_matured": matured,
        "ledger": ledger,
        "triggers": triggers,
        "retrain_triggered": triggered,
        "learning_cycle_id": cycle_id,
        "limitations": LIMITATIONS,
    }


# --------------------------------------------------------- read-side helpers
_RUN_COLS = [
    "run_id",
    "idempotency_key",
    "track_key",
    "status",
    "stop_reason",
    "target_status",
    "qwen_model_id",
    "qwen_model_fingerprint",
    "qwen_runtime",
    "prompt_template_sha256",
    "snapshot_id",
    "snapshot_sha256",
    "split_sha256",
    "eval_window",
    "budgets",
    "seed",
    "code_version",
    "plan_validator_version",
    "created_at",
    "finished_at",
]


def _fetch_run(conn: Any, where: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
    row = conn.execute(
        f"select run_id::text, idempotency_key, track_key, status, stop_reason, target_status, "
        f"qwen_model_id, qwen_model_fingerprint, qwen_runtime, prompt_template_sha256, snapshot_id::text, "
        f"snapshot_sha256, split_sha256, eval_window, budgets, seed, code_version, plan_validator_version, "
        f"created_at, finished_at from {SCHEMA}.kg_qwen_agent_runs {where}",
        params,
    ).fetchone()
    if not row:
        return None
    return {c: _val(row, c, i) for i, c in enumerate(_RUN_COLS)}


def _latest_run_for_track(conn: Any, track_key: str) -> dict[str, Any] | None:
    return _fetch_run(conn, "where track_key = %s order by created_at desc limit 1", (track_key,))


def _dataset_profile(rows: Sequence[TrainingRow], frequency: str) -> dict[str, Any]:
    """Tuning-window-only profile via agent_tools when available."""
    try:
        return cast("dict[str, Any]", _kq_module("agent_tools").dataset_profile(list(rows), frequency))
    except Exception:
        values = [r.value for r in rows if r.value is not None]
        return {
            "n_obs": len(rows),
            "n_observed": len(values),
            "first_period": rows[0].period_start.isoformat() if rows else None,
            "last_period": rows[-1].period_start if rows else None,
            "mean": (sum(values) / len(values)) if values else None,
            "note": "FALLBACK_PROFILE_AGENT_TOOLS_UNAVAILABLE",
        }


def autotuning_overview(conn: Any, track_key: str) -> dict[str, Any] | None:
    """Aggregate read for the overview endpoint (read-only)."""
    track = _track(track_key)
    run = _latest_run_for_track(conn, track_key)
    snap = conn.execute(
        f"select snapshot_id::text, demo_key, n_obs, first_period, last_period, data_through, "
        f"missing_periods, missing_semantics, dataset_sha256, snapshot_sha256 "
        f"from {SCHEMA}.kg_pt_demo_snapshots where demo_key = %s order by created_at desc limit 1",
        (track_key,),
    ).fetchone()
    snapshot = None
    profile = None
    if snap:
        cols = [
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
        snapshot = {c: _val(snap, c, i) for i, c in enumerate(cols)}
        try:
            frozen = pt_pipeline._load_frozen_rows(conn, snapshot["snapshot_id"])
            tuning_rows = frozen[: track.get("tuning_n", len(frozen))]
            profile = _dataset_profile(tuning_rows, track["frequency"])
        except Exception:
            profile = None
    if run is None and snapshot is None:
        return None

    plans: list[dict[str, Any]] = []
    rounds: list[dict[str, Any]] = []
    evaluation: dict[str, Any] | None = None
    feedback_summary: dict[str, Any] | None = None
    learning_cycles: list[dict[str, Any]] = []
    champion_decisions: list[dict[str, Any]] = []
    recommendation = None
    shadow_run_id = None
    if run is not None:
        plans = autotuning_plans(conn, run["run_id"])
        rounds = autotuning_rounds(conn, run["run_id"])
        evaluation = autotuning_evaluation(conn, run["run_id"])
        fb = autotuning_feedback(conn, run["run_id"])
        if fb:
            feedback_summary = fb.get("summary")
            shadow_run_id = fb.get("shadow_run_id")
        if shadow_run_id:
            cyc_rows = conn.execute(
                f"select l.learning_cycle_id::text, l.cycle_type, l.status, l.min_matured_feedback, "
                f"l.matured_feedback_count, l.evidence_sha256, l.created_at "
                f"from {SCHEMA}.kg_learning_cycles l where l.run_id = %s order by l.created_at",
                (shadow_run_id,),
            ).fetchall()
            cyc_cols = [
                "learning_cycle_id",
                "cycle_type",
                "status",
                "min_matured_feedback",
                "matured_feedback_count",
                "evidence_sha256",
                "created_at",
            ]
            learning_cycles = [{c: _val(r, c, i) for i, c in enumerate(cyc_cols)} for r in cyc_rows]
            dec_rows = conn.execute(
                f"select decision_id::text, learning_cycle_id::text, champion_adapter_id, champion_source, "
                f"kg_model_status, reason, promotion_applied, created_at "
                f"from {SCHEMA}.kg_champion_decisions where run_id = %s order by created_at",
                (shadow_run_id,),
            ).fetchall()
            dec_cols = [
                "decision_id",
                "learning_cycle_id",
                "champion_adapter_id",
                "champion_source",
                "kg_model_status",
                "reason",
                "promotion_applied",
                "created_at",
            ]
            champion_decisions = [{c: _val(r, c, i) for i, c in enumerate(dec_cols)} for r in dec_rows]
        try:
            rec_row = conn.execute(
                f"select r.recommendation_id::text, r.adapter_id, r.total_score, "
                f"r.parameter_confidence_score, r.eligibility_status "
                f"from {SCHEMA}.kg_recommendations r where r.recommendation_id = %s",
                (DEMO_RECOMMENDATION_ID,),
            ).fetchone()
            if rec_row:
                rec_cols = [
                    "recommendation_id",
                    "adapter_id",
                    "total_score",
                    "parameter_confidence_score",
                    "eligibility_status",
                ]
                recommendation = {c: _val(rec_row, c, i) for i, c in enumerate(rec_cols)}
        except Exception:
            recommendation = None

    best_round = None
    for r in rounds:
        if r.get("target_met"):
            best_round = r
            break
    if best_round is None and rounds:
        with_wape = [r for r in rounds if r.get("best_val_wape") is not None]
        best_round = min(with_wape, key=lambda r: r["best_val_wape"]) if with_wape else rounds[-1]
    target_gap = None
    if best_round and best_round.get("best_val_agreement_pct") is not None:
        target_gap = round(70.0 - float(best_round["best_val_agreement_pct"]), 6)

    # ---- frontend contract alignment for the kg-qwen-loop dashboard block ----
    key_parts = track_key.split(":")
    track_card = {
        "country": key_parts[0],
        "disease": key_parts[1],
        "indicator": key_parts[2],
        "frequency": track["frequency"],
        "identity": track["eval_identity"],
        "is_fresh": track["eval_identity"] == "FRESH_HELDOUT_LOCKED",
    }
    data_profile = None
    if profile:
        data_profile = {**profile, "seasonal_strength": profile.get("seasonal_autocorrelation")}
    trials: list[dict[str, Any]] = []
    parameter_ranges: list[dict[str, Any]] = []
    champion: dict[str, Any] | None = None
    if run is not None:
        trials = autotuning_trials(conn, run["run_id"], None)
        parameter_ranges = _parameter_ranges_from_plans(plans, track["frequency"])
        champion = _champion_card(champion_decisions)
    if evaluation is not None:
        paired = evaluation.get("paired") or {}
        baseline_wapes = [float(r["baseline_best_wape"]) for r in rounds if r.get("baseline_best_wape") is not None]
        best_baseline_wape = min(baseline_wapes) if baseline_wapes else None
        evaluation["is_fresh"] = evaluation.get("identity") == "FRESH_HELDOUT_LOCKED"
        paired_improvement = paired.get("relative_improvement_vs_best_baseline")
        if paired_improvement is not None and paired.get("paired_sample_match"):
            evaluation["beats_baseline"] = float(paired_improvement) >= 0.01
            evaluation["best_baseline_wape"] = paired.get("best_baseline_wape")
            evaluation["best_baseline_id"] = paired.get("best_baseline_id")
        else:
            evaluation["beats_baseline"] = (
                best_baseline_wape is not None
                and evaluation.get("wape") is not None
                and float(evaluation["wape"]) <= best_baseline_wape * (1.0 - 0.01)
            )
            evaluation["best_baseline_wape"] = best_baseline_wape
    # Global held-out governance for this track+snapshot (may exist without a run)
    heldout_registry: list[dict[str, Any]] = []
    if snapshot is not None:
        reg_rows = conn.execute(
            f"{_REGISTRY_SELECT} from {SCHEMA}.kg_qwen_heldout_registry "
            f"where track_key = %s and snapshot_sha256 = %s order by registered_at",
            (track_key, snapshot["snapshot_sha256"]),
        ).fetchall()
        heldout_registry = [_registry_row_dict(r) for r in reg_rows]
    return {
        "track_key": track_key,
        "frequency": track["frequency"],
        "eval_identity": track["eval_identity"],
        "eval_flags": list(track["eval_flags"]),
        "track": track_card,
        "run": run,
        "shadow_run_id": shadow_run_id,
        "snapshot": snapshot,
        "dataset_profile": profile,
        "data_profile": data_profile,
        "recommendation": recommendation,
        "recommendation_reason_zh": _recommendation_reason_zh(recommendation, track_card),
        "plans": plans,
        "parameter_ranges": parameter_ranges,
        "rounds": rounds,
        "trials": trials,
        "evaluation": evaluation,
        "champion": champion,
        "feedback_summary": feedback_summary,
        "learning_cycles": learning_cycles,
        "champion_decisions": champion_decisions,
        "heldout_registry": heldout_registry,
        "target": {"wape_max": 0.30, "agreement_pct_min": 70.0, "gap_to_70pct": target_gap},
        "limitations": LIMITATIONS,
    }


def _parameter_ranges_from_plans(plans: list[dict[str, Any]], frequency: str) -> list[dict[str, Any]]:
    """Contract-vs-plan parameter ranges from the newest accepted plan (read-only)."""
    accepted = [p for p in plans if p.get("validation_status") == "ACCEPTED" and p.get("plan_json")]
    if not accepted:
        return []
    plan_json = accepted[-1]["plan_json"]
    if isinstance(plan_json, str):
        try:
            plan_json = json.loads(plan_json)
        except ValueError:
            return []
    spaces = plan_json.get("parameter_spaces") or []
    try:
        contracts_ext = _kq_module("contracts_ext")
    except Exception:
        contracts_ext = None
    out: list[dict[str, Any]] = []
    for space in spaces:
        adapter_id = space.get("adapter_id")
        parameter_name = space.get("parameter_name")
        row: dict[str, Any] = {
            "adapter_id": adapter_id,
            "parameter_name": parameter_name,
            "contract_lower": None,
            "contract_upper": None,
            "contract_values": None,
            "plan_lower": space.get("lower"),
            "plan_upper": space.get("upper"),
            "plan_values": space.get("values"),
            "source": space.get("parameter_source"),
        }
        if contracts_ext is not None:
            for c in contracts_ext.all_contracts_for(str(adapter_id), frequency):
                if c.parameter_name == parameter_name:
                    row["contract_lower"] = c.lower_bound
                    row["contract_upper"] = c.upper_bound
                    row["contract_values"] = c.discrete_candidates
                    break
        out.append(row)
    return out


def _champion_card(champion_decisions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Champion-of-record card: prefer the decision that crowned a champion
    (EXECUTED_CHAMPION); reconcile decisions only record incumbent retention."""
    if not champion_decisions:
        return None
    crowned = [d for d in champion_decisions if d.get("kg_model_status") == "EXECUTED_CHAMPION"]
    latest = crowned[-1] if crowned else champion_decisions[-1]
    source_display = {
        "KG_RECOMMENDED_MODEL": "TUNED_MODEL",
        "EMPIRICAL_BASELINE_OVERRIDE": "BASELINE",
    }.get(str(latest.get("champion_source")), latest.get("champion_source"))
    return {
        "champion_adapter_id": latest.get("champion_adapter_id"),
        "champion_source": source_display,
        "reason": latest.get("reason"),
        "kg_model_status": latest.get("kg_model_status"),
        "promotion_applied": latest.get("promotion_applied"),
    }


def _recommendation_reason_zh(recommendation: dict[str, Any] | None, track_card: dict[str, Any]) -> list[str]:
    """Chinese reason lines for the initial KG recommendation (or its absence)."""
    if recommendation:
        return [
            f"KG 推荐模型为 {recommendation.get('adapter_id')}：执行前证据总分 "
            f"{recommendation.get('total_score')}/100，参数可信度 "
            f"{recommendation.get('parameter_confidence_score')}/100。",
            "该分数代表知识图谱证据充分程度，不是预测准确率；最终优劣由滚动原点验证与一次性 held-out 评估决定。",
            "参数来源：KG 中没有这些参数的论文提取证据，全部如实标注为实现默认值"
            "（IMPLEMENTATION_DEFAULT）或数据画像规则（DATA_PROFILE_RULE）。",
        ]
    return [
        f"序列 {track_card['country']}/{track_card['disease']}/{track_card['indicator']} 在知识图谱中"
        "没有执行前推荐记录；初始候选来自白名单注册表（negbin_dglm.v1 与 4 个受控新候选），"
        "由 Qwen 在确定性校验器约束下规划搜索空间。",
        "KG 证据分数缺失不会被伪装成推荐理由；全部参数来源标注为 IMPLEMENTATION_DEFAULT 或 "
        "DATA_PROFILE_RULE，无论文提取声明。",
    ]


def autotuning_plans(conn: Any, run_id: str) -> list[dict[str, Any]]:
    """All tuning plans (with validation verdicts) for one run (read-only)."""
    rows = conn.execute(
        f"select plan_id::text, run_id::text, round_index, plan_source, plan_json, plan_sha256, "
        f"prompt_sha256, response_sha256, qwen_latency_ms, validation_status, validation_errors, created_at "
        f"from {SCHEMA}.kg_qwen_tuning_plans where run_id = %s order by round_index, created_at",
        (run_id,),
    ).fetchall()
    cols = [
        "plan_id",
        "run_id",
        "round_index",
        "plan_source",
        "plan_json",
        "plan_sha256",
        "prompt_sha256",
        "response_sha256",
        "qwen_latency_ms",
        "validation_status",
        "validation_errors",
        "created_at",
    ]
    return [{c: _val(r, c, i) for i, c in enumerate(cols)} for r in rows]


def autotuning_rounds(conn: Any, run_id: str) -> list[dict[str, Any]]:
    """All rounds (with best-trial metrics and stop reasons) for one run."""
    rows = conn.execute(
        f"select round_id::text, run_id::text, round_index, status, trials_executed, best_adapter_id, "
        f"best_trial_id::text, best_val_wape, best_val_agreement_pct, best_val_coverage, baseline_best_wape, "
        f"improvement_vs_baseline, target_met, stop_reason, created_at, finished_at "
        f"from {SCHEMA}.kg_qwen_tuning_rounds where run_id = %s order by round_index",
        (run_id,),
    ).fetchall()
    cols = [
        "round_id",
        "run_id",
        "round_index",
        "status",
        "trials_executed",
        "best_adapter_id",
        "best_trial_id",
        "best_val_wape",
        "best_val_agreement_pct",
        "best_val_coverage",
        "baseline_best_wape",
        "improvement_vs_baseline",
        "target_met",
        "stop_reason",
        "created_at",
        "finished_at",
    ]
    return [{c: _val(r, c, i) for i, c in enumerate(cols)} for r in rows]


def autotuning_trials(conn: Any, run_id: str, round_index: int | None) -> list[dict[str, Any]]:
    """All trials (including failures) for one run, optionally one round."""
    sql = (
        f"select trial_id::text, run_id::text, round_index, trial_index, adapter_id, params, params_sha256, "
        f"parameter_source, origin_sha256s, per_horizon_metrics, common_wape, agreement_pct, coverage, "
        f"avg_interval_width, complexity_penalty, objective_value, gates, gate_pass, failure_reason, "
        f"artifact_sha256, dataset_sha256, trial_sha256, elapsed_ms, created_at "
        f"from {SCHEMA}.kg_qwen_trials where run_id = %s"
    )
    params: tuple[Any, ...] = (run_id,)
    if round_index is not None:
        sql += " and round_index = %s"
        params = (run_id, round_index)
    sql += " order by round_index, adapter_id, trial_index"
    rows = conn.execute(sql, params).fetchall()
    cols = [
        "trial_id",
        "run_id",
        "round_index",
        "trial_index",
        "adapter_id",
        "params",
        "params_sha256",
        "parameter_source",
        "origin_sha256s",
        "per_horizon_metrics",
        "common_wape",
        "agreement_pct",
        "coverage",
        "avg_interval_width",
        "complexity_penalty",
        "objective_value",
        "gates",
        "gate_pass",
        "failure_reason",
        "artifact_sha256",
        "dataset_sha256",
        "trial_sha256",
        "elapsed_ms",
        "created_at",
    ]
    return [{c: _val(r, c, i) for i, c in enumerate(cols)} for r in rows]


def autotuning_evaluation(conn: Any, run_id: str) -> dict[str, Any] | None:
    """Held-out evaluation row(s) for one run (identity + exactly-once state)."""
    rows = conn.execute(
        f"select eval_id::text, run_id::text, track, identity, snapshot_sha256, eval_window, locked_at, "
        f"evaluated_at, eval_count, metrics, wape, agreement_pct, coverage, mae, rmse, smape, mase, "
        f"registry_id::text "
        f"from {SCHEMA}.kg_qwen_evaluation_sets where run_id = %s order by locked_at",
        (run_id,),
    ).fetchall()
    if not rows:
        return None
    cols = [
        "eval_id",
        "run_id",
        "track",
        "identity",
        "snapshot_sha256",
        "eval_window",
        "locked_at",
        "evaluated_at",
        "eval_count",
        "metrics",
        "wape",
        "agreement_pct",
        "coverage",
        "mae",
        "rmse",
        "smape",
        "mase",
        "registry_id",
    ]
    evals = [{("window" if c == "eval_window" else c): _val(r, c, i) for i, c in enumerate(cols)} for r in rows]
    primary = evals[0]
    registry = None
    if primary.get("registry_id"):
        registry = _fetch_registry(conn, str(primary["registry_id"]))
    metrics = primary.get("metrics") or {}
    classification = metrics.get("classification")
    classification_note = metrics.get("classification_note_zh")
    if classification is None and primary["identity"] in REPLAY_IDENTITIES:
        # pre-governance rows reclassified by 0043: derive the display label
        # from the (corrected) identity instead of leaving the page blank
        classification = "REPLAY_ONLY_NOT_FRESH"
        classification_note = (
            "历史重复评估结果，不构成独立盲测认证。"
            if primary["identity"] == IDENTITY_RETROSPECTIVE
            else "历史回放基准（复用既有测试窗），不构成独立盲测认证。"
        )
    return {
        "run_id": run_id,
        "identity": primary["identity"],
        "eval_count": primary["eval_count"],
        "wape": primary["wape"],
        "agreement_pct": primary["agreement_pct"],
        "coverage": primary["coverage"],
        "mae": primary["mae"],
        "rmse": primary["rmse"],
        "smape": primary["smape"],
        "mase": primary["mase"],
        "window": primary["window"],
        "locked_at": primary["locked_at"],
        "evaluated_at": primary["evaluated_at"],
        "metrics": primary["metrics"],
        "registry_id": primary.get("registry_id"),
        "registry": registry,
        "classification": classification,
        "classification_note_zh": classification_note,
        "time_chain": metrics.get("time_chain"),
        "certification": metrics.get("certification"),
        "paired": metrics.get("paired"),
        "evaluations": evals,
        "limitations": LIMITATIONS,
    }


def autotuning_feedback(conn: Any, run_id: str) -> dict[str, Any] | None:
    """Feedback maturation progress for one run (read-only)."""
    run = _fetch_run(conn, "where run_id = %s", (run_id,))
    if run is None:
        return None
    shadow = _shadow_run_for_agent(
        conn, str(run["idempotency_key"]), str(run["track_key"]), str(run["snapshot_sha256"])
    )
    if not shadow:
        return {"run_id": run_id, "shadow_run_id": None, "summary": None, "rows": [], "limitations": LIMITATIONS}
    shadow_run_id = str(_val(shadow, "run_id", 0))
    summary = _feedback_ledger(conn, shadow_run_id)
    rows = conn.execute(
        f"select feedback_id::text, forecast_value_id::text, adapter_id, target_period, actual_value, "
        f"actual_revision, absolute_error, interval_hit, feedback_maturity, actual_available_at "
        f"from {SCHEMA}.kg_forecast_objective_feedback where run_id = %s "
        f"order by target_period, feedback_maturity limit 500",
        (shadow_run_id,),
    ).fetchall()
    cols = [
        "feedback_id",
        "forecast_value_id",
        "adapter_id",
        "target_period",
        "actual_value",
        "actual_revision",
        "absolute_error",
        "interval_hit",
        "feedback_maturity",
        "actual_available_at",
    ]
    feedback_rows = [{c: _val(r, c, i) for i, c in enumerate(cols)} for r in rows]
    return {
        "run_id": run_id,
        "shadow_run_id": shadow_run_id,
        "summary": summary,
        "rows": feedback_rows,
        "limitations": LIMITATIONS,
    }
