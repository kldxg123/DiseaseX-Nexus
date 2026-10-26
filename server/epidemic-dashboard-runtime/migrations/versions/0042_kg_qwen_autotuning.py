"""Add KG Qwen autonomous-tuning tables.

Revision ID: 0042_kg_qwen_autotuning
Revises: 0041_kg_parameter_tuning

Additive-only migration for the Qwen-planned autotuning + feedback loop:
agent runs (one per idempotency key), per-round tuning plans (Qwen proposals,
validation verdicts included), round aggregates, trials and locked held-out
evaluation sets (eval_count gates exactly-once evaluation at the privilege
level of a guarded UPDATE). No historical table is altered; trusted
observations, recommendations, promotions and active pointers stay untouched.
Tuning plans and trials are append-only at the grant level (select+insert
only); runs/rounds/evaluation sets get narrow UPDATE grants on lifecycle
columns only. Fully reversible via downgrade() (drops only the 5 new tables,
FK-safe order).
"""

from alembic import op

revision = "0042_kg_qwen_autotuning"
down_revision = "0041_kg_parameter_tuning"
branch_labels = None
depends_on = None

SCHEMA = "epidemic_kg"

TABLES = (
    "kg_qwen_agent_runs",
    "kg_qwen_tuning_plans",
    "kg_qwen_tuning_rounds",
    "kg_qwen_trials",
    "kg_qwen_evaluation_sets",
)


def upgrade() -> None:
    # --- agent runs (one per idempotency key; content-addressed) ---
    op.execute(
        f"""
        create table if not exists {SCHEMA}.kg_qwen_agent_runs (
          run_id uuid primary key,
          idempotency_key text not null unique,
          track_key text not null,
          status text not null default 'RUNNING',
          stop_reason text,
          target_status text,
          qwen_model_id text,
          qwen_model_fingerprint text,
          qwen_runtime text,
          prompt_template_sha256 text,
          snapshot_id uuid,
          snapshot_sha256 text,
          split_sha256 text,
          eval_window jsonb,
          budgets jsonb,
          seed integer,
          code_version text,
          plan_validator_version text,
          created_at timestamptz not null default now(),
          finished_at timestamptz
        )
        """
    )
    # --- per-round Qwen tuning plans (append-only: validation verdict stored at insert) ---
    op.execute(
        f"""
        create table if not exists {SCHEMA}.kg_qwen_tuning_plans (
          plan_id uuid primary key,
          run_id uuid not null references {SCHEMA}.kg_qwen_agent_runs(run_id),
          round_index integer not null,
          plan_source text not null,
          plan_json jsonb not null,
          plan_sha256 text not null,
          prompt_sha256 text,
          response_sha256 text,
          qwen_latency_ms integer,
          validation_status text not null,
          validation_errors jsonb,
          created_at timestamptz not null default now(),
          unique(run_id, round_index, plan_sha256)
        )
        """
    )
    # --- per-round aggregate results ---
    op.execute(
        f"""
        create table if not exists {SCHEMA}.kg_qwen_tuning_rounds (
          round_id uuid primary key,
          run_id uuid not null references {SCHEMA}.kg_qwen_agent_runs(run_id),
          round_index integer not null,
          status text not null default 'RUNNING',
          trials_executed integer,
          best_adapter_id text,
          best_trial_id uuid,
          best_val_wape double precision,
          best_val_agreement_pct double precision,
          best_val_coverage double precision,
          baseline_best_wape double precision,
          improvement_vs_baseline double precision,
          target_met boolean,
          stop_reason text,
          created_at timestamptz not null default now(),
          finished_at timestamptz,
          unique(run_id, round_index)
        )
        """
    )
    # --- trials (every trial persisted, including failures; append-only) ---
    op.execute(
        f"""
        create table if not exists {SCHEMA}.kg_qwen_trials (
          trial_id uuid primary key,
          run_id uuid not null references {SCHEMA}.kg_qwen_agent_runs(run_id),
          round_index integer not null,
          trial_index integer not null,
          adapter_id text not null,
          params jsonb not null,
          params_sha256 text not null,
          parameter_source jsonb,
          origin_sha256s jsonb,
          per_horizon_metrics jsonb,
          common_wape double precision,
          agreement_pct double precision,
          coverage double precision,
          avg_interval_width double precision,
          complexity_penalty double precision,
          objective_value double precision,
          gates jsonb,
          gate_pass boolean,
          failure_reason text,
          artifact_sha256 text,
          dataset_sha256 text,
          trial_sha256 text not null,
          elapsed_ms integer,
          created_at timestamptz not null default now(),
          unique(run_id, round_index, trial_index, adapter_id)
        )
        """
    )
    # --- locked held-out evaluation sets (eval_count gates exactly-once evaluation) ---
    op.execute(
        f"""
        create table if not exists {SCHEMA}.kg_qwen_evaluation_sets (
          eval_id uuid primary key,
          run_id uuid references {SCHEMA}.kg_qwen_agent_runs(run_id),
          track text not null,
          identity text not null,
          snapshot_sha256 text not null,
          eval_window jsonb not null,
          locked_at timestamptz not null default now(),
          evaluated_at timestamptz,
          eval_count integer not null default 0,
          metrics jsonb,
          wape double precision,
          agreement_pct double precision,
          coverage double precision,
          mae double precision,
          rmse double precision,
          smape double precision,
          mase double precision,
          created_at timestamptz not null default now(),
          unique(run_id, identity)
        )
        """
    )
    op.execute(f"create index if not exists kg_qwen_tuning_plans_run_idx on {SCHEMA}.kg_qwen_tuning_plans(run_id)")
    op.execute(f"create index if not exists kg_qwen_tuning_rounds_run_idx on {SCHEMA}.kg_qwen_tuning_rounds(run_id)")
    op.execute(f"create index if not exists kg_qwen_trials_run_idx on {SCHEMA}.kg_qwen_trials(run_id)")
    op.execute(
        f"create index if not exists kg_qwen_evaluation_sets_run_idx on {SCHEMA}.kg_qwen_evaluation_sets(run_id)"
    )
    # read role + app write role grants (idempotent, same pattern as 0041)
    op.execute(f"grant select on all tables in schema {SCHEMA} to epidemic_dashboard_ro")
    for table in TABLES:
        op.execute(f"grant select, insert on {SCHEMA}.{table} to epidemic_app")
    # narrow lifecycle updates only; plans/trials stay append-only (no update grant)
    op.execute(
        f"grant update (status, stop_reason, target_status, finished_at) "
        f"on {SCHEMA}.kg_qwen_agent_runs to epidemic_app"
    )
    op.execute(
        f"grant update (status, trials_executed, best_adapter_id, best_trial_id, best_val_wape, "
        f"best_val_agreement_pct, best_val_coverage, baseline_best_wape, improvement_vs_baseline, "
        f"target_met, stop_reason, finished_at) on {SCHEMA}.kg_qwen_tuning_rounds to epidemic_app"
    )
    op.execute(
        f"grant update (evaluated_at, eval_count, metrics, wape, agreement_pct, coverage, mae, rmse, "
        f"smape, mase) on {SCHEMA}.kg_qwen_evaluation_sets to epidemic_app"
    )
    # run_id is claimable exactly once (NULL -> run_id) by the eval lock; still no update on content cols
    op.execute(f"grant update (run_id) on {SCHEMA}.kg_qwen_evaluation_sets to epidemic_app")


def downgrade() -> None:
    op.execute(f"drop table if exists {SCHEMA}.kg_qwen_evaluation_sets")
    op.execute(f"drop table if exists {SCHEMA}.kg_qwen_trials")
    op.execute(f"drop table if exists {SCHEMA}.kg_qwen_tuning_rounds")
    op.execute(f"drop table if exists {SCHEMA}.kg_qwen_tuning_plans")
    op.execute(f"drop table if exists {SCHEMA}.kg_qwen_agent_runs")
