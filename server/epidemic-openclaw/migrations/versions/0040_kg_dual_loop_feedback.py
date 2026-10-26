"""Add KG dual-dataset forecast feedback loop tables.

Revision ID: 0040_kg_dual_loop_feedback
Revises: 0039_kg_r3c_incremental_build

Additive-only migration for the controlled Shadow execution loop:
model artifacts, backtest folds/predictions/metrics, objective + analyst
feedback, learning cycles and champion decisions. No historical table is
altered; trusted observations, recommendations, graph versions and forecast
history stay untouched. Fully reversible via downgrade().
"""

from alembic import op

revision = "0040_kg_dual_loop_feedback"
down_revision = "0039_kg_r3c_incremental_build"
branch_labels = None
depends_on = None

SCHEMA = "epidemic_kg"


def upgrade() -> None:
    op.execute(
        f"""
        create table if not exists {SCHEMA}.kg_shadow_model_artifacts (
          artifact_id uuid primary key,
          run_id uuid not null references {SCHEMA}.kg_forecast_shadow_runs(run_id),
          adapter_id text not null,
          adapter_version text not null,
          seed integer not null,
          state jsonb not null,
          train_row_count integer not null,
          train_sha256 text not null,
          artifact_sha256 text not null unique,
          created_at timestamptz not null default now()
        )
        """
    )
    op.execute(
        f"""
        create table if not exists {SCHEMA}.kg_shadow_backtest_folds (
          fold_id uuid primary key,
          run_id uuid not null references {SCHEMA}.kg_forecast_shadow_runs(run_id),
          adapter_id text not null,
          origin_index integer not null,
          origin_period date not null,
          train_row_count integer not null,
          train_sha256 text not null,
          model_artifact_sha256 text not null,
          status text not null default 'OK',
          created_at timestamptz not null default now(),
          unique(run_id, adapter_id, origin_period)
        )
        """
    )
    op.execute(
        f"""
        create table if not exists {SCHEMA}.kg_shadow_backtest_predictions (
          prediction_id uuid primary key,
          fold_id uuid not null references {SCHEMA}.kg_shadow_backtest_folds(fold_id),
          run_id uuid not null references {SCHEMA}.kg_forecast_shadow_runs(run_id),
          adapter_id text not null,
          origin_index integer not null,
          origin_period date not null,
          target_period date not null,
          horizon integer not null,
          yhat numeric,
          lower_bound numeric,
          upper_bound numeric,
          interval_level numeric,
          interval_method text,
          actual_value numeric,
          actual_observation_id text,
          actual_revision integer,
          abs_error numeric,
          squared_error numeric,
          deviance numeric,
          direction_correct boolean,
          model_artifact_sha256 text not null,
          not_eligible text,
          prediction_sha256 text not null,
          created_at timestamptz not null default now(),
          unique(run_id, adapter_id, origin_period, horizon)
        )
        """
    )
    op.execute(
        f"""
        create table if not exists {SCHEMA}.kg_shadow_model_metrics (
          metric_id uuid primary key,
          run_id uuid not null references {SCHEMA}.kg_forecast_shadow_runs(run_id),
          adapter_id text not null,
          horizon integer not null,
          mae numeric,
          rmse numeric,
          wape numeric,
          mase numeric,
          deviance numeric,
          interval_coverage numeric,
          interval_avg_width numeric,
          forecast_row_count integer not null default 0,
          labeled_row_count integer not null default 0,
          missing_label_count integer not null default 0,
          metrics_sha256 text not null,
          created_at timestamptz not null default now(),
          unique(run_id, adapter_id, horizon)
        )
        """
    )
    op.execute(
        f"""
        create table if not exists {SCHEMA}.kg_forecast_objective_feedback (
          feedback_id uuid primary key,
          run_id uuid not null references {SCHEMA}.kg_forecast_shadow_runs(run_id),
          forecast_value_id uuid,
          adapter_id text not null,
          target_period date not null,
          actual_observation_id text,
          actual_value numeric,
          actual_revision integer,
          actual_available_at timestamptz,
          absolute_error numeric,
          squared_error numeric,
          interval_hit boolean,
          direction_correct boolean,
          feedback_maturity text not null default 'AWAITING_ACTUAL',
          reconciliation_sha256 text not null unique,
          created_at timestamptz not null default now()
        )
        """
    )
    op.execute(
        f"""
        create table if not exists {SCHEMA}.kg_analyst_feedback (
          feedback_id uuid primary key,
          run_id uuid not null references {SCHEMA}.kg_forecast_shadow_runs(run_id),
          recommendation_id uuid references {SCHEMA}.kg_recommendations(recommendation_id),
          actor text not null,
          rating integer,
          decision_code text not null check (decision_code in (
            'USEFUL','NOT_USEFUL','EXPLANATION_CLEAR','EXPLANATION_UNCLEAR',
            'DATA_QUALITY_CONCERN','NEEDS_MORE_EVIDENCE')),
          comment text,
          comment_sha256 text not null,
          created_at timestamptz not null default now(),
          unique(run_id, actor, decision_code, comment_sha256)
        )
        """
    )
    op.execute(
        f"""
        create table if not exists {SCHEMA}.kg_learning_cycles (
          learning_cycle_id uuid primary key,
          run_id uuid not null references {SCHEMA}.kg_forecast_shadow_runs(run_id),
          recommendation_id uuid references {SCHEMA}.kg_recommendations(recommendation_id),
          cycle_type text not null,
          status text not null default 'COMPLETED',
          min_matured_feedback integer not null default 1,
          matured_feedback_count integer not null default 0,
          evidence jsonb not null default '{{}}'::jsonb,
          evidence_sha256 text not null,
          created_at timestamptz not null default now(),
          unique(run_id, cycle_type)
        )
        """
    )
    op.execute(
        f"""
        create table if not exists {SCHEMA}.kg_champion_decisions (
          decision_id uuid primary key,
          learning_cycle_id uuid not null references {SCHEMA}.kg_learning_cycles(learning_cycle_id),
          run_id uuid not null references {SCHEMA}.kg_forecast_shadow_runs(run_id),
          champion_adapter_id text not null,
          champion_source text not null check (champion_source in (
            'KG_RECOMMENDED_MODEL','EMPIRICAL_BASELINE_OVERRIDE')),
          kg_model_status text not null,
          reason text not null,
          decided_by text not null default 'SYSTEM_BACKTEST',
          promotion_applied boolean not null default false,
          created_at timestamptz not null default now(),
          unique(learning_cycle_id)
        )
        """
    )
    for table in (
        "kg_shadow_model_artifacts",
        "kg_shadow_backtest_folds",
        "kg_shadow_backtest_predictions",
        "kg_shadow_model_metrics",
        "kg_forecast_objective_feedback",
        "kg_analyst_feedback",
        "kg_learning_cycles",
        "kg_champion_decisions",
    ):
        op.execute(f"create index if not exists {table}_run_idx on {SCHEMA}.{table}(run_id)")
    op.execute(
        f"create index if not exists kg_forecast_objective_feedback_maturity_idx "
        f"on {SCHEMA}.kg_forecast_objective_feedback(feedback_maturity, target_period)"
    )
    # read role + app write role grants (idempotent)
    op.execute(f"grant select on all tables in schema {SCHEMA} to epidemic_dashboard_ro")
    for table in (
        "kg_shadow_model_artifacts",
        "kg_shadow_backtest_folds",
        "kg_shadow_backtest_predictions",
        "kg_shadow_model_metrics",
        "kg_forecast_objective_feedback",
        "kg_analyst_feedback",
        "kg_learning_cycles",
        "kg_champion_decisions",
    ):
        op.execute(f"grant select, insert, update on {SCHEMA}.{table} to epidemic_app")
    op.execute(f"grant insert, update on {SCHEMA}.kg_forecast_shadow_runs to epidemic_app")
    op.execute(f"grant insert on {SCHEMA}.kg_forecast_shadow_values to epidemic_app")
    # execution path needs read access to the curated history view
    op.execute("grant select on epidemic.dashboard_history_series_v2 to epidemic_app")


def downgrade() -> None:
    op.execute(f"drop table if exists {SCHEMA}.kg_champion_decisions")
    op.execute(f"drop table if exists {SCHEMA}.kg_learning_cycles")
    op.execute(f"drop table if exists {SCHEMA}.kg_analyst_feedback")
    op.execute(f"drop table if exists {SCHEMA}.kg_forecast_objective_feedback")
    op.execute(f"drop table if exists {SCHEMA}.kg_shadow_model_metrics")
    op.execute(f"drop table if exists {SCHEMA}.kg_shadow_backtest_predictions")
    op.execute(f"drop table if exists {SCHEMA}.kg_shadow_backtest_folds")
    op.execute(f"drop table if exists {SCHEMA}.kg_shadow_model_artifacts")
