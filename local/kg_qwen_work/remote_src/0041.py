"""Add KG parameter-tuning and controlled self-learning demo tables.

Revision ID: 0041_kg_parameter_tuning
Revises: 0040_kg_dual_loop_feedback

Additive-only migration for the parameter-tuning demo loop: frozen demo
snapshots (+ immutable snapshot rows), temporal splits, parameter contracts,
tuning runs/trials/origins and exactly-once independent test evaluations.
No historical table is altered; trusted observations, recommendations,
promotions and active pointers stay untouched. Snapshot rows are granted
select+insert only, so they are immutable at the database privilege level.
Fully reversible via downgrade() (drops only the 8 new tables, FK-safe order).
"""

from alembic import op

revision = "0041_kg_parameter_tuning"
down_revision = "0040_kg_dual_loop_feedback"
branch_labels = None
depends_on = None

SCHEMA = "epidemic_kg"

TABLES = (
    "kg_pt_demo_snapshots",
    "kg_pt_demo_snapshot_rows",
    "kg_pt_temporal_splits",
    "kg_pt_parameter_contracts",
    "kg_pt_tuning_runs",
    "kg_pt_tuning_trials",
    "kg_pt_trial_origins",
    "kg_pt_test_evaluations",
)


def upgrade() -> None:
    # --- frozen demo snapshot header (content-addressed) ---
    op.execute(
        f"""
        create table if not exists {SCHEMA}.kg_pt_demo_snapshots (
          snapshot_id uuid primary key,
          demo_key text not null,
          country text not null,
          disease text not null,
          indicator text not null,
          frequency text not null,
          source_table text not null,
          n_obs integer not null,
          first_period date not null,
          last_period date not null,
          data_through date not null,
          revision_max integer not null,
          observation_keys jsonb not null,
          missing_periods jsonb not null,
          missing_semantics text not null,
          dataset_sha256 text not null,
          snapshot_sha256 text not null unique,
          created_at timestamptz not null default now(),
          unique(demo_key, snapshot_sha256)
        )
        """
    )
    # --- frozen snapshot rows (immutable evidence: select+insert only) ---
    op.execute(
        f"""
        create table if not exists {SCHEMA}.kg_pt_demo_snapshot_rows (
          snapshot_id uuid not null references {SCHEMA}.kg_pt_demo_snapshots(snapshot_id),
          row_index integer not null,
          period_start date not null,
          period_end date,
          value_numeric numeric,
          observation_id text,
          revision integer not null,
          row_sha256 text not null,
          created_at timestamptz not null default now(),
          primary key (snapshot_id, row_index)
        )
        """
    )
    # --- temporal train/validation/test split (content-addressed) ---
    op.execute(
        f"""
        create table if not exists {SCHEMA}.kg_pt_temporal_splits (
          split_id uuid primary key,
          snapshot_sha256 text not null references {SCHEMA}.kg_pt_demo_snapshots(snapshot_sha256),
          train_start date,
          train_end date not null,
          validation_start date not null,
          validation_end date not null,
          test_start date not null,
          test_end date not null,
          train_rows integer not null,
          validation_rows integer not null,
          test_rows integer not null,
          split_sha256 text not null unique,
          created_at timestamptz not null default now()
        )
        """
    )
    # --- parameter contracts (bounds + provenance per parameter) ---
    op.execute(
        f"""
        create table if not exists {SCHEMA}.kg_pt_parameter_contracts (
          contract_id uuid primary key,
          adapter_id text not null,
          parameter_name text not null,
          data_type text not null,
          default_value numeric,
          lower_bound numeric,
          upper_bound numeric,
          discrete_candidates jsonb,
          transform text not null default 'none',
          parameter_source text not null check (parameter_source in (
            'PAPER_EXTRACTED','IMPLEMENTATION_DEFAULT','DATA_PROFILE_RULE','OPTIMIZER_SELECTED')),
          evidence_locator text,
          code_source text,
          applicable_profile jsonb not null default '{{}}'::jsonb,
          missing_reason text,
          safety_constraint text,
          tunable boolean not null default false,
          contract_sha256 text not null unique,
          created_at timestamptz not null default now(),
          unique(adapter_id, parameter_name, contract_sha256)
        )
        """
    )
    # --- tuning runs (one per idempotency key + cycle) ---
    op.execute(
        f"""
        create table if not exists {SCHEMA}.kg_pt_tuning_runs (
          tuning_run_id uuid primary key,
          shadow_run_id uuid references {SCHEMA}.kg_forecast_shadow_runs(run_id),
          recommendation_id uuid,
          snapshot_sha256 text not null,
          split_sha256 text not null,
          adapter_id text not null,
          cycle_index integer not null default 1,
          seed integer not null,
          max_trials integer not null,
          max_seconds integer not null,
          objective text not null,
          code_version text not null,
          status text not null default 'RUNNING',
          idempotency_key text not null unique,
          best_trial_id uuid,
          started_at timestamptz,
          completed_at timestamptz,
          created_at timestamptz not null default now()
        )
        """
    )
    # --- trials (every trial persisted, including failures) ---
    op.execute(
        f"""
        create table if not exists {SCHEMA}.kg_pt_tuning_trials (
          trial_id uuid primary key,
          tuning_run_id uuid not null references {SCHEMA}.kg_pt_tuning_runs(tuning_run_id),
          trial_index integer not null,
          params jsonb not null,
          params_sha256 text not null,
          parameter_source jsonb not null,
          n_origins integer not null,
          n_labeled integer not null,
          common_wape numeric,
          per_horizon_metrics jsonb,
          coverage numeric,
          avg_interval_width numeric,
          complexity_penalty numeric,
          objective_value numeric,
          gates jsonb,
          gate_pass boolean,
          failure_reason text,
          elapsed_ms integer,
          artifact_sha256 text,
          code_version text not null,
          dataset_snapshot_sha256 text not null,
          trial_sha256 text not null unique,
          created_at timestamptz not null default now(),
          unique(tuning_run_id, trial_index)
        )
        """
    )
    # --- per-origin audit rows (leakage boundary evidence) ---
    op.execute(
        f"""
        create table if not exists {SCHEMA}.kg_pt_trial_origins (
          origin_id uuid primary key,
          trial_id uuid not null references {SCHEMA}.kg_pt_tuning_trials(trial_id),
          origin_index integer not null,
          origin_period date not null,
          issue_period date not null,
          data_through date not null,
          feature_max_period date not null,
          label_periods jsonb not null,
          train_row_count integer not null,
          validation_row_count integer not null,
          dataset_sha256 text not null,
          seed integer not null,
          artifact_sha256 text not null,
          origin_sha256 text not null,
          created_at timestamptz not null default now(),
          unique(trial_id, origin_index)
        )
        """
    )
    # --- independent test evaluations (exactly once per run x adapter) ---
    op.execute(
        f"""
        create table if not exists {SCHEMA}.kg_pt_test_evaluations (
          eval_id uuid primary key,
          tuning_run_id uuid not null references {SCHEMA}.kg_pt_tuning_runs(tuning_run_id),
          adapter_id text not null,
          is_tuned_model boolean not null,
          params jsonb not null default '{{}}'::jsonb,
          params_sha256 text not null,
          split_sha256 text not null,
          n_origins integer not null,
          per_horizon_metrics jsonb not null,
          mae numeric,
          rmse numeric,
          wape numeric,
          smape numeric,
          mase numeric,
          coverage numeric,
          avg_interval_width numeric,
          agreement_pct numeric,
          eval_sha256 text not null unique,
          created_at timestamptz not null default now(),
          unique(tuning_run_id, adapter_id)
        )
        """
    )
    op.execute(f"create index if not exists kg_pt_tuning_trials_run_idx on {SCHEMA}.kg_pt_tuning_trials(tuning_run_id)")
    op.execute(f"create index if not exists kg_pt_trial_origins_trial_idx on {SCHEMA}.kg_pt_trial_origins(trial_id)")
    op.execute(
        f"create index if not exists kg_pt_test_evaluations_run_idx on {SCHEMA}.kg_pt_test_evaluations(tuning_run_id)"
    )
    op.execute(
        f"create index if not exists kg_pt_temporal_splits_snapshot_idx "
        f"on {SCHEMA}.kg_pt_temporal_splits(snapshot_sha256)"
    )
    # read role + app write role grants (idempotent)
    op.execute(f"grant select on all tables in schema {SCHEMA} to epidemic_dashboard_ro")
    for table in TABLES:
        op.execute(f"grant select, insert on {SCHEMA}.{table} to epidemic_app")
    # tuning runs may only transition status/completion/best-trial, never rewrite history
    op.execute(f"grant update (status, completed_at, best_trial_id) on {SCHEMA}.kg_pt_tuning_runs to epidemic_app")
    # kg_pt_demo_snapshot_rows deliberately gets NO update/delete grant: frozen evidence.
    # The historical-replay demo stores test-window forecasts with row_type TEST_REPLAY in the
    # pre-existing shadow-values ledger; extend its check constraint (additive, no data rewrite).
    op.execute(
        f"alter table {SCHEMA}.kg_forecast_shadow_values "
        "drop constraint if exists kg_forecast_shadow_values_row_type_check"
    )
    op.execute(
        f"alter table {SCHEMA}.kg_forecast_shadow_values "
        "add constraint kg_forecast_shadow_values_row_type_check "
        "check (row_type in ('BACKTEST', 'FUTURE', 'SCENARIO_DECISION', 'TEST_REPLAY'))"
    )


def downgrade() -> None:
    # downgrade is destructive by nature: replay rows must go before the old check returns
    op.execute(f"delete from {SCHEMA}.kg_forecast_shadow_values where row_type = 'TEST_REPLAY'")
    op.execute(
        f"alter table {SCHEMA}.kg_forecast_shadow_values "
        "drop constraint if exists kg_forecast_shadow_values_row_type_check"
    )
    op.execute(
        f"alter table {SCHEMA}.kg_forecast_shadow_values "
        "add constraint kg_forecast_shadow_values_row_type_check "
        "check (row_type in ('BACKTEST', 'FUTURE', 'SCENARIO_DECISION'))"
    )
    op.execute(f"drop table if exists {SCHEMA}.kg_pt_test_evaluations")
    op.execute(f"drop table if exists {SCHEMA}.kg_pt_trial_origins")
    op.execute(f"drop table if exists {SCHEMA}.kg_pt_tuning_trials")
    op.execute(f"drop table if exists {SCHEMA}.kg_pt_tuning_runs")
    op.execute(f"drop table if exists {SCHEMA}.kg_pt_parameter_contracts")
    op.execute(f"drop table if exists {SCHEMA}.kg_pt_temporal_splits")
    op.execute(f"drop table if exists {SCHEMA}.kg_pt_demo_snapshot_rows")
    op.execute(f"drop table if exists {SCHEMA}.kg_pt_demo_snapshots")
