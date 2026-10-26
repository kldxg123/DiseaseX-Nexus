"""R4 KG schema adoption, evidence queue, and non-promotion shadow forecasts.

Revision ID: 0032_r4_kg_schema_evidence_shadow
Revises: 0031_kg_p1_paper1_quarantine
Create Date: 2026-08-05
"""

from __future__ import annotations

from alembic import op


revision = "0032_r4_kg_schema_evidence_shadow"
down_revision = "0031_kg_p1_paper1_quarantine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # R3 created these objects directly in live DB. R4 formally adopts them.
    op.execute("create schema if not exists epidemic_kg")
    op.execute(
        """
        create table if not exists epidemic_kg.kg_document_pages (
          page_id uuid primary key,
          document_node_id uuid not null references epidemic_kg.kg_nodes(node_id),
          physical_page integer not null check (physical_page >= 1),
          printed_page_label text not null default '',
          extraction_tool text not null,
          extraction_status text not null,
          text_content text not null,
          text_sha256 text not null check (text_sha256 ~ '^[0-9a-f]{64}$'),
          char_count integer not null check (char_count >= 0),
          created_at timestamptz not null default now(),
          unique(document_node_id, physical_page)
        )
        """
    )
    op.execute(
        """
        create table if not exists epidemic_kg.kg_document_chunks (
          chunk_id uuid primary key,
          page_id uuid not null references epidemic_kg.kg_document_pages(page_id),
          document_node_id uuid not null references epidemic_kg.kg_nodes(node_id),
          physical_page integer not null check (physical_page >= 1),
          chunk_index integer not null check (chunk_index >= 0),
          chunk_text text not null,
          chunk_sha256 text not null check (chunk_sha256 ~ '^[0-9a-f]{64}$'),
          token_estimate integer not null check (token_estimate >= 0),
          created_at timestamptz not null default now(),
          unique(document_node_id, physical_page, chunk_index)
        )
        """
    )
    op.execute(
        """
        create index if not exists kg_document_chunks_text_tsv_idx
        on epidemic_kg.kg_document_chunks using gin (to_tsvector('english', chunk_text))
        """
    )
    op.execute(
        """
        create table if not exists epidemic_kg.kg_pending_claim_candidates (
          candidate_id uuid primary key,
          document_node_id uuid not null references epidemic_kg.kg_nodes(node_id),
          work_node_id uuid references epidemic_kg.kg_nodes(node_id),
          page_id uuid references epidemic_kg.kg_document_pages(page_id),
          chunk_id uuid references epidemic_kg.kg_document_chunks(chunk_id),
          claim_type text not null,
          disease_text text,
          model_text text,
          parameter_text text,
          evidence_anchor text not null,
          evidence_sha256 text not null check (evidence_sha256 ~ '^[0-9a-f]{64}$'),
          status text not null default 'MACHINE_EXTRACTED_PENDING_REVIEW',
          recommendation_eligible boolean not null default false,
          properties jsonb not null default '{}'::jsonb,
          created_at timestamptz not null default now(),
          unique(document_node_id, claim_type, evidence_sha256)
        )
        """
    )
    for ddl in [
        "alter table epidemic_kg.kg_pending_claim_candidates add column if not exists candidate_generation text not null default 'R3_LEGACY'",
        "alter table epidemic_kg.kg_pending_claim_candidates add column if not exists legacy_candidate_id uuid",
        "alter table epidemic_kg.kg_pending_claim_candidates add column if not exists disease_relation text",
        "alter table epidemic_kg.kg_pending_claim_candidates add column if not exists disease_text_normalized text",
        "alter table epidemic_kg.kg_pending_claim_candidates add column if not exists model_name_normalized text",
        "alter table epidemic_kg.kg_pending_claim_candidates add column if not exists parameter_name text",
        "alter table epidemic_kg.kg_pending_claim_candidates add column if not exists parameter_value text",
        "alter table epidemic_kg.kg_pending_claim_candidates add column if not exists parameter_unit text",
        "alter table epidemic_kg.kg_pending_claim_candidates add column if not exists scope_text text",
        "alter table epidemic_kg.kg_pending_claim_candidates add column if not exists locator_status text not null default 'LEGACY_LOCATOR_INCOMPLETE'",
        "alter table epidemic_kg.kg_pending_claim_candidates add column if not exists physical_page integer",
        "alter table epidemic_kg.kg_pending_claim_candidates add column if not exists chunk_index integer",
        "alter table epidemic_kg.kg_pending_claim_candidates add column if not exists extraction_version text not null default 'R3_KEYWORD_LEGACY'",
        "alter table epidemic_kg.kg_pending_claim_candidates add column if not exists review_ready boolean not null default false",
        "alter table epidemic_kg.kg_pending_claim_candidates add column if not exists superseded_by uuid",
    ]:
        op.execute(ddl)
    op.execute("create index if not exists kg_pending_claim_candidates_r4_status_idx on epidemic_kg.kg_pending_claim_candidates(candidate_generation,status,review_ready,recommendation_eligible)")
    op.execute("create index if not exists kg_pending_claim_candidates_locator_idx on epidemic_kg.kg_pending_claim_candidates(document_node_id,physical_page,chunk_index)")

    op.execute(
        """
        create table if not exists epidemic_kg.kg_work_identity_supersessions (
          supersession_id uuid primary key,
          old_work_node_id uuid not null references epidemic_kg.kg_nodes(node_id),
          new_work_node_id uuid not null references epidemic_kg.kg_nodes(node_id),
          reason_code text not null,
          old_canonical_key text not null,
          new_canonical_key text not null,
          provenance jsonb not null default '{}'::jsonb,
          created_at timestamptz not null default now(),
          unique(old_work_node_id, new_work_node_id, reason_code)
        )
        """
    )
    op.execute(
        """
        create table if not exists epidemic_kg.kg_forecast_shadow_runs (
          run_id uuid primary key,
          run_key text not null unique,
          idempotency_key text not null unique,
          mode text not null check (mode in ('SHADOW','STAGED','DEMO','SCENARIO','NOT_APPLICABLE')),
          status text not null,
          disease_slug text not null,
          country_alpha3 text not null,
          metric_code text not null,
          cadence text not null,
          data_through date,
          model_name text not null,
          fallback_reason text,
          promotion_eligible boolean not null default false,
          active_pointer_changed boolean not null default false,
          trusted_rows_changed integer not null default 0,
          profile jsonb not null default '{}'::jsonb,
          selection_trace jsonb not null default '{}'::jsonb,
          created_at timestamptz not null default now(),
          updated_at timestamptz not null default now()
        )
        """
    )
    op.execute(
        """
        create table if not exists epidemic_kg.kg_forecast_shadow_values (
          value_id uuid primary key,
          run_id uuid not null references epidemic_kg.kg_forecast_shadow_runs(run_id),
          target_period_start date,
          target_period_end date,
          horizon integer not null default 1,
          yhat numeric,
          lower_value numeric,
          upper_value numeric,
          actual_value numeric,
          is_observed boolean not null default false,
          is_imputed boolean not null default false,
          imputation_method text,
          source_observation_ids uuid[] not null default '{}',
          row_type text not null check (row_type in ('BACKTEST','FUTURE','SCENARIO_DECISION')),
          properties jsonb not null default '{}'::jsonb,
          created_at timestamptz not null default now(),
          unique(run_id, target_period_start, horizon, row_type)
        )
        """
    )
    op.execute("create index if not exists kg_forecast_shadow_values_run_idx on epidemic_kg.kg_forecast_shadow_values(run_id, row_type, target_period_start)")


def downgrade() -> None:
    op.execute("drop table if exists epidemic_kg.kg_forecast_shadow_values")
    op.execute("drop table if exists epidemic_kg.kg_forecast_shadow_runs")
    op.execute("drop table if exists epidemic_kg.kg_work_identity_supersessions")
    # R3 adopted text/candidate tables are intentionally preserved on downgrade
    # to avoid losing live evidence staging rows. The migration is reversible for
    # R4-created shadow objects while data-owner cleanup handles adopted R3 data.
