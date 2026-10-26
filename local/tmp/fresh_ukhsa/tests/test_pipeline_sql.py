# ruff: noqa: E501
"""Static migration checks + pure-function tests for kg_qwen (no database).

The migration file is checked as TEXT: presence of the five new tables, the
correct revision chain, the 0041-style grants and a complete downgrade that
drops only the new tables. The pipeline payload builders are pure functions
and are tested with plain namespace fakes — nothing here opens a connection.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from epidemic_core.kg_qwen import pipeline as kq_pipeline

ROOT = Path(__file__).resolve().parents[2]
_LOCAL = ROOT / "new_migrations" / "0042_kg_qwen_autotuning.py"
_REMOTE = ROOT / "migrations" / "versions" / "0042_kg_qwen_autotuning.py"
MIGRATION = _LOCAL if _LOCAL.exists() else _REMOTE

KQ_TABLES = (
    "kg_qwen_agent_runs",
    "kg_qwen_tuning_plans",
    "kg_qwen_tuning_rounds",
    "kg_qwen_trials",
    "kg_qwen_evaluation_sets",
)


@pytest.fixture(scope="module")
def migration_text() -> str:
    # the migration builds DDL via f-strings with a {SCHEMA} placeholder
    return MIGRATION.read_text(encoding="utf-8").replace("{SCHEMA}", "epidemic_kg")


# ------------------------------------------------------------ migration text
def test_migration_revision_chain(migration_text: str) -> None:
    assert 'revision = "0042_kg_qwen_autotuning"' in migration_text
    assert 'down_revision = "0041_kg_parameter_tuning"' in migration_text


def test_migration_creates_all_five_tables(migration_text: str) -> None:
    for table in KQ_TABLES:
        assert f"create table if not exists epidemic_kg.{table}" in migration_text, table


def test_migration_key_columns(migration_text: str) -> None:
    # exactly-once evaluation gate
    assert "eval_count integer not null default 0" in migration_text
    # SPEC 3.11 uniques
    assert "unique(run_id, round_index, plan_sha256)" in migration_text
    assert "unique(run_id, round_index)" in migration_text
    assert "unique(run_id, round_index, trial_index, adapter_id)" in migration_text
    assert "unique(run_id, identity)" in migration_text
    assert "idempotency_key text not null unique" in migration_text
    # numeric metric columns are double precision (0041 style for the new tables)
    assert "best_val_wape double precision" in migration_text
    assert "common_wape double precision" in migration_text
    assert "wape double precision" in migration_text
    # jsonb payloads + timestamptz defaults
    assert "plan_json jsonb not null" in migration_text
    assert "eval_window jsonb not null" in migration_text
    assert migration_text.count("created_at timestamptz not null default now()") >= 5


def test_migration_grants_follow_0041(migration_text: str) -> None:
    assert "grant select on all tables in schema epidemic_kg to epidemic_dashboard_ro" in migration_text
    # per-table grants are emitted by a loop over TABLES (0041 style)
    assert "grant select, insert on epidemic_kg.{table} to epidemic_app" in migration_text
    for table in KQ_TABLES:
        assert f'"{table}"' in migration_text, table  # TABLES tuple drives the grant loop
    # plans and trials are append-only: update grants cover lifecycle columns of
    # runs/rounds/evaluation-sets only
    update_grants = re.findall(r"grant update\b.*?on epidemic_kg\.(\w+) to epidemic_app", migration_text, re.S)
    assert set(update_grants) == {"kg_qwen_agent_runs", "kg_qwen_tuning_rounds", "kg_qwen_evaluation_sets"}


def test_migration_downgrade_drops_only_new_tables(migration_text: str) -> None:
    drops = re.findall(r"drop table if exists epidemic_kg\.(\w+)", migration_text)
    assert sorted(drops) == sorted(KQ_TABLES)
    # FK-safe order: children before parents
    assert drops[0] == "kg_qwen_evaluation_sets"
    assert drops[-1] == "kg_qwen_agent_runs"
    # no other table is dropped or altered
    assert "kg_pt_" not in " ".join(drops)
    assert "alter table" not in migration_text.lower()
    assert "kg_forecast_shadow" not in migration_text


# ----------------------------------------------------------------- constants
def test_track_b_window_constants_sum_to_445() -> None:
    assert kq_pipeline.TRACK_B_TUNING_WEEKS == 393
    assert kq_pipeline.TRACK_B_HIDDEN_WEEKS == 52
    assert kq_pipeline.TRACK_B_TUNING_WEEKS + kq_pipeline.TRACK_B_HIDDEN_WEEKS == 445
    assert kq_pipeline.TRACK_B_TOTAL_WEEKS == 445
    assert kq_pipeline.TRACK_B["train_n"] + kq_pipeline.TRACK_B["val_n"] == kq_pipeline.TRACK_B_TUNING_WEEKS
    assert kq_pipeline.TRACK_B["test_n"] == 0


def test_track_identities() -> None:
    assert kq_pipeline.TRACK_A["eval_identity"] == "REUSED_HISTORICAL_BENCHMARK"
    assert "NOT_FRESH_HELDOUT" in kq_pipeline.TRACK_A["eval_flags"]
    assert kq_pipeline.TRACK_B["eval_identity"] == "FRESH_HELDOUT_LOCKED"
    assert (kq_pipeline.TRACK_A["train_n"], kq_pipeline.TRACK_A["val_n"], kq_pipeline.TRACK_A["test_n"]) == (168, 36, 36)


def test_unknown_track_rejected() -> None:
    with pytest.raises(ValueError, match="KQ_UNKNOWN_TRACK"):
        kq_pipeline._track("XXX:nope:nope:day")


def test_limitations_cover_required_disclosures() -> None:
    text = "\n".join(kq_pipeline.LIMITATIONS)
    assert "Shadow" in text
    assert "回放" in text
    assert "WAPE" in text
    assert "NOT_FRESH_HELDOUT" in text
    assert "医疗" in text


# ------------------------------------------------------- pure payload builders
def _periods(n: int, start: date = date(2018, 1, 1), step_days: int = 7) -> list[date]:
    return [start + timedelta(days=step_days * i) for i in range(n)]


def test_track_b_eval_window_payload_is_hidden_tail() -> None:
    periods = _periods(445)
    window = kq_pipeline.track_eval_window_payload(kq_pipeline.TRACK_B_KEY, periods)
    assert window["identity"] == "FRESH_HELDOUT_LOCKED"
    assert window["n_periods"] == 52
    assert window["start_period"] == periods[393].isoformat()
    assert window["end_period"] == periods[444].isoformat()
    assert window["frequency"] == "week"


def test_track_a_eval_window_payload_is_reused_test_tail() -> None:
    periods = _periods(240, start=date(2005, 1, 1), step_days=31)
    window = kq_pipeline.track_eval_window_payload(kq_pipeline.TRACK_A_KEY, periods)
    assert window["identity"] == "REUSED_HISTORICAL_BENCHMARK"
    assert "NOT_FRESH_HELDOUT" in window["flags"]
    assert window["n_periods"] == 36
    assert window["start_period"] == periods[204].isoformat()


def test_budgets_payload_uses_spec_budget() -> None:
    budgets = kq_pipeline.budgets_payload()
    assert budgets["max_rounds"] == 3
    assert budgets["max_trials_per_round"] == 50
    assert budgets["max_total_minutes"] == 45
    assert budgets["trial_timeout_seconds"] == 120
    assert budgets["seed"] == 20260811
    assert budgets["target_wape"] == pytest.approx(0.30)
    assert budgets["target_agreement_pct"] == pytest.approx(70.0)
    assert budgets["coverage_min"] == pytest.approx(0.85)


def test_run_row_payload_is_content_addressed() -> None:
    payload = kq_pipeline.run_row_payload(kq_pipeline.TRACK_A_KEY, "s" * 64)
    assert payload["track_key"] == kq_pipeline.TRACK_A_KEY
    assert payload["snapshot_sha256"] == "s" * 64
    assert payload["code_version"] == "kg_qwen.v1"
    assert payload["budgets"]["seed"] == 20260811


def _fake_round_result() -> Any:
    proposal = SimpleNamespace(
        plan={"plan_version": "v1", "candidate_models": ["negbin_dglm.v1"]},
        plan_source="QWEN",
        prompt_sha256="p" * 64,
        response_sha256="r" * 64,
        latency_ms=1234,
    )
    trial = SimpleNamespace(
        trial_index=0,
        params={"discount": 0.95},
        params_sha256="t" * 64,
        parameter_source={"discount": "IMPLEMENTATION_DEFAULT"},
        origins=[SimpleNamespace(origin_sha256="o" * 64)],
        per_horizon_metrics={"1": {"wape": 0.3}},
        common_wape=0.28,
        coverage=0.9,
        avg_interval_width=40.0,
        complexity_penalty=0.0,
        objective_value=0.28,
        gates={"coverage": True},
        gate_pass=True,
        failure_reason=None,
        artifact_sha256="a" * 64,
        dataset_snapshot_sha256="d" * 64,
        trial_sha256="x" * 64,
        elapsed_ms=100,
    )
    return SimpleNamespace(
        round_index=1,
        proposal=proposal,
        validation_status="ACCEPTED",
        validation_errors=[],
        trials=[trial],
        best_adapter_id="negbin_dglm.v1",
        best_trial_sha256="x" * 64,
        best_val_wape=0.28,
        best_val_agreement_pct=72.0,
        best_val_coverage=0.9,
        baseline_best_wape=0.31,
        improvement_vs_baseline=0.097,
        target_met=True,
        stop_reason="TARGET_MET",
        trials_executed=1,
    )


def test_plan_row_payload() -> None:
    payload = kq_pipeline.plan_row_payload("run-1", 1, _fake_round_result())
    assert payload["run_id"] == "run-1"
    assert payload["round_index"] == 1
    assert payload["plan_source"] == "QWEN"
    assert len(payload["plan_sha256"]) == 64
    assert payload["validation_status"] == "ACCEPTED"
    assert payload["qwen_latency_ms"] == 1234


def test_round_row_payload() -> None:
    payload = kq_pipeline.round_row_payload("run-1", _fake_round_result(), "trial-uuid")
    assert payload["best_trial_id"] == "trial-uuid"
    assert payload["best_val_wape"] == pytest.approx(0.28)
    assert payload["target_met"] is True
    assert payload["stop_reason"] == "TARGET_MET"
    assert payload["trials_executed"] == 1


def test_trial_row_payload_derives_agreement_and_origin_shas() -> None:
    rr = _fake_round_result()
    payload = kq_pipeline.trial_row_payload("run-1", 1, "negbin_dglm.v1", rr.trials[0])
    assert payload["agreement_pct"] == pytest.approx(72.0)
    assert payload["origin_sha256s"] == ["o" * 64]
    assert payload["trial_sha256"] == "x" * 64
    assert payload["gate_pass"] is True
    assert payload["params_sha256"] == "t" * 64


def test_trial_row_payload_tolerates_sparse_trial() -> None:
    """A baseline-evaluation trial without origin/params attributes still builds."""
    sparse = SimpleNamespace(trial_index=3, common_wape=None, gate_pass=False)
    payload = kq_pipeline.trial_row_payload("run-1", 2, "seasonal_naive", sparse)
    assert payload["adapter_id"] == "seasonal_naive"
    assert payload["params"] == {}
    assert payload["agreement_pct"] is None
    assert payload["origin_sha256s"] == []
    assert len(payload["trial_sha256"]) == 64


def test_eval_metrics_payload() -> None:
    cells = [
        {
            "horizon": 1,
            "yhat": 10.0,
            "actual_value": 12.0,
            "lower_bound": 5.0,
            "upper_bound": 15.0,
            "mase_denominator": 2.0,
        },
        {
            "horizon": 2,
            "yhat": 8.0,
            "actual_value": 8.0,
            "lower_bound": 4.0,
            "upper_bound": 6.0,
            "mase_denominator": 2.0,
        },
    ]
    metrics = kq_pipeline._eval_metrics_payload(cells)
    assert metrics["labeled_cells"] == 2
    assert metrics["wape"] == pytest.approx(2.0 / 20.0)
    assert metrics["agreement_pct"] == pytest.approx(90.0)
    assert metrics["coverage"] == pytest.approx(0.5)
    assert metrics["mase"] == pytest.approx((1.0 + 0.0) / 2)
    assert set(metrics["per_horizon"]) == {"0", "1", "2", "3", "4"}


def test_pipeline_module_imports_without_db() -> None:
    # module-level surface required by SPEC section 6
    for name in (
        "execute_autotuning",
        "autotuning_overview",
        "autotuning_plans",
        "autotuning_rounds",
        "autotuning_trials",
        "autotuning_evaluation",
        "autotuning_feedback",
        "reconcile_feedback",
    ):
        assert callable(getattr(kq_pipeline, name)), name
