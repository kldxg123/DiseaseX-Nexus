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
    # kg_nodes / kg_edges predate alembic (created by R3 directly in the live DB) and were
    # historically only *referenced* here, which made the chain non-replayable from an empty
    # database. Create-if-missing keeps the adoption idempotent and never rewrites live data.
    op.execute(
        """
        create table if not exists epidemic_kg.kg_nodes (
          node_id uuid primary key,
          canonical_key text not null unique,
          node_type text not null,
          display_name text not null,
          status text not null default 'ACTIVE',
          trust_status text not null default 'UNSPECIFIED',
          review_status text not null default 'PENDING_REVIEW',
          quarantine boolean not null default false,
          evidence_eligible boolean not null default false,
          properties jsonb not null default '{}'::jsonb,
          created_at timestamptz not null default now(),
          updated_at timestamptz not null default now(),
          check (node_type in ('WORK','DOCUMENT_VERSION','CLAIM','MODEL_OR_METHOD','TASK','TARGET',
            'METRIC','BASELINE_OR_COMPARATOR','NUMERIC_RESULT','LIMITATION','DISEASE','PATHOGEN',
            'GEOGRAPHY','INDICATOR','DATA_SOURCE','SOURCE_CONTRACT','SOURCE_FETCH','RAW_ARTIFACT',
            'NORMALIZATION_RUN','DATA_SERIES','QUALITY_ASSESSMENT','DATASET_SNAPSHOT','REVIEW_DECISION'))
        )
        """
    )
    op.execute(
        """
        create table if not exists epidemic_kg.kg_edges (
          edge_id uuid primary key,
          subject_node_id uuid not null references epidemic_kg.kg_nodes(node_id) on delete restrict,
          predicate text not null,
          object_node_id uuid not null references epidemic_kg.kg_nodes(node_id) on delete restrict,
          relation_status text not null default 'SYSTEM_DERIVED',
          evidence_eligible boolean not null default false,
          properties jsonb not null default '{}'::jsonb,
          created_at timestamptz not null default now(),
          unique(subject_node_id, predicate, object_node_id),
          check (predicate in ('IS_VERSION_OF','RELATED_PREPRINT_VERSION_OF','REPORTS','SUPPORTED_BY',
            'ABOUT_DISEASE','ABOUT_GEOGRAPHY','USES_SOURCE','USES_DATASET','APPLIES_TO_TASK',
            'PREDICTS_TARGET','EVALUATED_WITH','COMPARED_WITH','REPORTS_RESULT','HAS_LIMITATION',
            'DERIVED_FROM','NORMALIZED_BY','ASSESSED_BY','INCLUDED_IN_SNAPSHOT','HAS_REVIEW_DECISION')),
          check (relation_status in ('DIRECTLY_REPORTED','MANUALLY_INFERRED','SYSTEM_DERIVED',
            'QUARANTINED','UNSUPPORTED'))
        )
        """
    )
    op.execute("create index if not exists kg_edges_object_idx on epidemic_kg.kg_edges(object_node_id, predicate)")
    op.execute("create index if not exists kg_edges_subject_idx on epidemic_kg.kg_edges(subject_node_id, predicate)")
    op.execute("create index if not exists kg_nodes_display_name_idx on epidemic_kg.kg_nodes(lower(display_name))")
    op.execute(
        "create index if not exists kg_nodes_type_status_idx "
        "on epidemic_kg.kg_nodes(node_type, review_status, quarantine, evidence_eligible)"
    )
    op.execute("grant select on epidemic_kg.kg_nodes to epidemic_dashboard_ro")
    op.execute("grant select, insert, update on epidemic_kg.kg_nodes to epidemic_app")
    op.execute("grant select on epidemic_kg.kg_edges to epidemic_dashboard_ro")
    op.execute("grant select, insert, update on epidemic_kg.kg_edges to epidemic_app")
    # Remaining R3-era base tables that later migrations reference but no migration
    # created. DDL taken from the live-DB pg_dump; create-if-missing makes empty-DB
    # replay work and is a no-op on the live DB. Ordered so every FK target in this
    # block is created before the FK that references it.
    op.execute(
        """
        create table if not exists epidemic_kg.kg_ingestion_runs (
          ingestion_run_id uuid primary key,
          run_key text not null unique,
          input_sha256 text not null,
          code_version text not null,
          started_at timestamptz not null,
          finished_at timestamptz,
          status text not null check (status in ('STARTED','COMPLETE','FAILED')),
          counts jsonb not null default '{}'::jsonb
        )
        """
    )
    op.execute(
        """
        create table if not exists epidemic_kg.kg_claims (
          claim_id uuid primary key,
          canonical_key text not null unique,
          work_node_id uuid references epidemic_kg.kg_nodes(node_id) on delete restrict,
          document_node_id uuid references epidemic_kg.kg_nodes(node_id) on delete restrict,
          claim_category text not null,
          claim_status text not null check (claim_status in ('DIRECTLY_REPORTED',
            'UNSUPPORTED_AFTER_MANUAL_REVIEW','NOT_EVALUATED_MISSING_CORRECT_VERSION')),
          subject_text text,
          predicate_text text,
          object_text text,
          review_status text not null default 'VALIDATED' check (review_status in ('VALIDATED',
            'MACHINE_EXTRACTED_PENDING_REVIEW','PENDING_REVIEW','QUARANTINED')),
          evidence_eligible boolean not null default false,
          quarantine boolean not null default false,
          metric_name text,
          numeric_value text,
          unit text,
          comparator_or_baseline text,
          direction text,
          dataset_or_split text,
          forecast_horizon text,
          properties jsonb not null default '{}'::jsonb,
          created_at timestamptz not null default now(),
          check ((claim_status = 'NOT_EVALUATED_MISSING_CORRECT_VERSION') = quarantine
            or claim_status <> 'NOT_EVALUATED_MISSING_CORRECT_VERSION')
        )
        """
    )
    op.execute(
        """
        create table if not exists epidemic_kg.kg_evidence_spans (
          evidence_span_id uuid primary key,
          document_node_id uuid not null references epidemic_kg.kg_nodes(node_id) on delete restrict,
          physical_page integer not null check (physical_page > 0),
          printed_page_label text not null,
          section_label text not null,
          table_or_figure text,
          raw_anchor text not null,
          anchor_start integer not null check (anchor_start >= 0),
          anchor_end integer not null,
          page_text_sha256 text not null,
          anchor_sha256 text not null,
          source_pdf_sha256 text not null,
          evidence_eligible boolean not null default true,
          quarantine boolean not null default false,
          properties jsonb not null default '{}'::jsonb,
          created_at timestamptz not null default now(),
          unique(document_node_id, physical_page, anchor_start, anchor_end, anchor_sha256),
          check (anchor_end > anchor_start)
        )
        """
    )
    op.execute(
        """
        create table if not exists epidemic_kg.kg_claim_evidence (
          claim_id uuid not null references epidemic_kg.kg_claims(claim_id) on delete restrict,
          evidence_span_id uuid not null references epidemic_kg.kg_evidence_spans(evidence_span_id) on delete restrict,
          relation_status text not null default 'SUPPORTED_BY',
          primary key (claim_id, evidence_span_id)
        )
        """
    )
    op.execute(
        """
        create table if not exists epidemic_kg.kg_aliases (
          alias_id uuid primary key,
          node_id uuid not null references epidemic_kg.kg_nodes(node_id) on delete restrict,
          alias text not null,
          alias_type text not null default 'display',
          unique(node_id, alias, alias_type)
        )
        """
    )
    op.execute(
        """
        create table if not exists epidemic_kg.kg_quarantine_items (
          quarantine_item_id uuid primary key,
          node_id uuid references epidemic_kg.kg_nodes(node_id) on delete restrict,
          claim_id uuid references epidemic_kg.kg_claims(claim_id) on delete restrict,
          reason_code text not null,
          eligible boolean not null default false check (eligible = false),
          created_at timestamptz not null default now(),
          properties jsonb not null default '{}'::jsonb
        )
        """
    )
    op.execute(
        """
        create table if not exists epidemic_kg.kg_source_bindings (
          source_binding_id uuid primary key,
          node_id uuid not null references epidemic_kg.kg_nodes(node_id) on delete restrict,
          source_schema text not null,
          source_table text not null,
          source_pk_repr text not null,
          source_row_hash text not null,
          trust_status text not null,
          ingestion_run_id uuid references epidemic_kg.kg_ingestion_runs(ingestion_run_id) on delete restrict,
          created_at timestamptz not null default now(),
          unique(node_id, source_schema, source_table, source_pk_repr)
        )
        """
    )
    op.execute(
        """
        create table if not exists epidemic_kg.kg_review_events (
          review_event_id uuid primary key,
          target_type text not null,
          target_id uuid not null,
          decision text not null,
          decision_at timestamptz not null default now(),
          reviewer text not null default 'KG_P1_DETERMINISTIC_SEED',
          properties jsonb not null default '{}'::jsonb
        )
        """
    )
    op.execute(
        "create index if not exists kg_claims_doc_category_idx "
        "on epidemic_kg.kg_claims(document_node_id, claim_category, claim_status)"
    )
    op.execute(
        "create index if not exists kg_evidence_document_page_idx "
        "on epidemic_kg.kg_evidence_spans(document_node_id, physical_page)"
    )
    op.execute(
        "create index if not exists kg_source_bindings_source_idx "
        "on epidemic_kg.kg_source_bindings(source_schema, source_table)"
    )
    op.execute("grant select on epidemic_kg.kg_aliases to epidemic_dashboard_ro")
    op.execute("grant select, insert, update on epidemic_kg.kg_aliases to epidemic_app")
    op.execute("grant select on epidemic_kg.kg_claim_evidence to epidemic_dashboard_ro")
    op.execute("grant select, insert, update on epidemic_kg.kg_claim_evidence to epidemic_app")
    op.execute("grant select on epidemic_kg.kg_claims to epidemic_dashboard_ro")
    op.execute("grant select, insert, update on epidemic_kg.kg_claims to epidemic_app")
    op.execute("grant select on epidemic_kg.kg_evidence_spans to epidemic_dashboard_ro")
    op.execute("grant select, insert, update on epidemic_kg.kg_evidence_spans to epidemic_app")
    op.execute("grant select on epidemic_kg.kg_ingestion_runs to epidemic_dashboard_ro")
    op.execute("grant select, insert, update on epidemic_kg.kg_ingestion_runs to epidemic_app")
    op.execute("grant select on epidemic_kg.kg_quarantine_items to epidemic_dashboard_ro")
    op.execute("grant select, insert, update on epidemic_kg.kg_quarantine_items to epidemic_app")
    op.execute("grant select on epidemic_kg.kg_review_events to epidemic_dashboard_ro")
    op.execute("grant select, insert, update on epidemic_kg.kg_review_events to epidemic_app")
    op.execute("grant select on epidemic_kg.kg_source_bindings to epidemic_dashboard_ro")
    op.execute("grant select, insert, update on epidemic_kg.kg_source_bindings to epidemic_app")
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
