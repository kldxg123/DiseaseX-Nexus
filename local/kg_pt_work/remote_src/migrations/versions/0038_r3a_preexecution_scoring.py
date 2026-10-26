"""R3A preexecution evidence scoring tables.

Revision ID: 0038_r3a_preexecution_scoring
Revises: 0037_r4r3cr2_semantic_evidence_closure
"""
from __future__ import annotations

revision = "0038_r3a_preexecution_scoring"
down_revision = "0037_r4r3cr2_semantic_evidence_closure"
branch_labels = None
depends_on = None

from alembic import op

def upgrade() -> None:
    op.execute("""
    create table if not exists epidemic_kg.kg_preexecution_data_profiles (
      profile_id uuid primary key, disease_slug text not null, country_alpha3 text not null, metric_code text not null, cadence text not null,
      period_start date, period_end date, observed_count integer not null, nonzero_count integer not null, source_count integer not null,
      profile jsonb not null, profile_sha256 text not null, created_at timestamptz not null default now(),
      unique(disease_slug,country_alpha3,metric_code,cadence)
    );
    create table if not exists epidemic_kg.kg_preexecution_model_blueprints (
      blueprint_id text primary key, model_family text not null, display_name text not null, is_baseline boolean not null default false, blueprint jsonb not null, blueprint_sha256 text not null, created_at timestamptz not null default now()
    );
    create table if not exists epidemic_kg.kg_preexecution_parameter_bundle_specs (
      bundle_id text primary key, blueprint_id text not null references epidemic_kg.kg_preexecution_model_blueprints(blueprint_id), parameter_confidence_score numeric not null, spec jsonb not null, spec_sha256 text not null, created_at timestamptz not null default now()
    );
    create table if not exists epidemic_kg.kg_preexecution_evidence_points (
      point_id text primary key, work_id text, document_version_id text, model_key text, disease_slug text, point_type text not null, polarity text not null, short_source_excerpt text not null, locator jsonb not null, raw_excerpt_sha256 text not null, normalized_excerpt_sha256 text not null, recommendation_eligible boolean not null default true, score_contribution numeric not null default 0, created_at timestamptz not null default now()
    );
    create table if not exists epidemic_kg.kg_preexecution_scoring_runs (
      run_id uuid primary key, score_type text not null check (score_type='PRE_EXECUTION_EVIDENCE_SCORE'), status text not null, policy jsonb not null, actually_executed boolean not null default false, fit_called boolean not null default false, predict_called boolean not null default false, forecast_run_id uuid null, created_at timestamptz not null default now()
    );
    create table if not exists epidemic_kg.kg_preexecution_model_candidates (
      candidate_id uuid primary key, run_id uuid not null references epidemic_kg.kg_preexecution_scoring_runs(run_id), profile_id uuid not null references epidemic_kg.kg_preexecution_data_profiles(profile_id), blueprint_id text not null references epidemic_kg.kg_preexecution_model_blueprints(blueprint_id), bundle_id text not null references epidemic_kg.kg_preexecution_parameter_bundle_specs(bundle_id), preexecution_evidence_score numeric not null, parameter_confidence_score numeric not null, evidence_coverage numeric not null, hard_gates text not null, rank integer not null, actually_executed boolean not null default false, fit_called boolean not null default false, predict_called boolean not null default false, local_backtest_status text not null default 'DEFERRED_TO_R3B', runtime_validation_status text not null default 'NOT_RUN_BY_PHASE_DESIGN', forecast_run_id uuid null, forecast_value_count integer not null default 0, local_metric_count integer not null default 0, formal_recommendation_eligible boolean not null default false, promotion_eligible boolean not null default false, active_pointer_eligible boolean not null default false, accuracy_claim_allowed boolean not null default false, explanation_zh text not null, created_at timestamptz not null default now(), unique(run_id, profile_id, blueprint_id)
    );
    create table if not exists epidemic_kg.kg_preexecution_score_components (
      component_id uuid primary key, candidate_id uuid not null references epidemic_kg.kg_preexecution_model_candidates(candidate_id), component_group text not null, rule_id text not null, score numeric not null, max_score numeric not null, contribution text not null, evidence_point_ids jsonb not null default '[]'::jsonb, created_at timestamptz not null default now(), unique(candidate_id, component_group, rule_id)
    );
    grant select on epidemic_kg.kg_preexecution_data_profiles, epidemic_kg.kg_preexecution_model_blueprints, epidemic_kg.kg_preexecution_parameter_bundle_specs, epidemic_kg.kg_preexecution_evidence_points, epidemic_kg.kg_preexecution_scoring_runs, epidemic_kg.kg_preexecution_model_candidates, epidemic_kg.kg_preexecution_score_components to epidemic_dashboard_ro;
    """)

def downgrade() -> None:
    op.execute("""
    drop table if exists epidemic_kg.kg_preexecution_score_components;
    drop table if exists epidemic_kg.kg_preexecution_model_candidates;
    drop table if exists epidemic_kg.kg_preexecution_scoring_runs;
    drop table if exists epidemic_kg.kg_preexecution_evidence_points;
    drop table if exists epidemic_kg.kg_preexecution_parameter_bundle_specs;
    drop table if exists epidemic_kg.kg_preexecution_model_blueprints;
    drop table if exists epidemic_kg.kg_preexecution_data_profiles;
    """)
