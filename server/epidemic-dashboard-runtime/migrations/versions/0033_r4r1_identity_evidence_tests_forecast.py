"""R4R1 identity closure, evidence contract, and shadow test metadata.

Revision ID: 0033_r4r1_identity_evidence_tests_forecast
Revises: 0032_r4_kg_schema_evidence_shadow
Create Date: 2026-08-05
"""

from __future__ import annotations

from alembic import op


revision = "0033_r4r1_identity_evidence_tests_forecast"
down_revision = "0032_r4_kg_schema_evidence_shadow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for ddl in [
        "alter table epidemic_kg.kg_pending_claim_candidates add column if not exists r4r1_disposition text",
        "alter table epidemic_kg.kg_pending_claim_candidates add column if not exists r4r1_evidence_contract_status text",
        "alter table epidemic_kg.kg_pending_claim_candidates add column if not exists source_pdf_sha256 text",
        "alter table epidemic_kg.kg_pending_claim_candidates add column if not exists evidence_text text",
        "alter table epidemic_kg.kg_pending_claim_candidates add column if not exists evidence_text_sha256 text",
        "alter table epidemic_kg.kg_pending_claim_candidates add column if not exists reverse_locator_verified boolean not null default false",
        "alter table epidemic_kg.kg_pending_claim_candidates add column if not exists relation_strength text",
        "alter table epidemic_kg.kg_pending_claim_candidates add column if not exists parameter_category text",
        "alter table epidemic_kg.kg_pending_claim_candidates add column if not exists parameter_value_source text",
        "alter table epidemic_kg.kg_pending_claim_candidates add column if not exists paper_status text",
    ]:
        op.execute(ddl)
    op.execute(
        """
        create table if not exists epidemic_kg.kg_document_evidence_status (
          document_node_id uuid primary key references epidemic_kg.kg_nodes(node_id),
          source_pdf_sha256 text not null,
          page_count integer not null,
          chunk_count integer not null,
          r4_candidate_count integer not null default 0,
          ready_candidate_count integer not null default 0,
          final_status text not null,
          status_reason text not null,
          checked_at timestamptz not null default now(),
          properties jsonb not null default '{}'::jsonb
        )
        """
    )
    op.execute(
        """
        create table if not exists epidemic_kg.kg_identity_quarantine (
          quarantine_id uuid primary key,
          work_node_id uuid not null references epidemic_kg.kg_nodes(node_id),
          canonical_key text not null,
          reason_code text not null,
          active_binding_count integer not null default 0,
          participates_in_recommendation boolean not null default false,
          evidence_eligible boolean not null default false,
          created_at timestamptz not null default now(),
          properties jsonb not null default '{}'::jsonb,
          unique(work_node_id, reason_code)
        )
        """
    )
    op.execute("create index if not exists kg_pending_claim_candidates_r4r1_disposition_idx on epidemic_kg.kg_pending_claim_candidates(candidate_generation,r4r1_disposition,r4r1_evidence_contract_status,reverse_locator_verified)")
    op.execute("create index if not exists kg_document_evidence_status_status_idx on epidemic_kg.kg_document_evidence_status(final_status)")


def downgrade() -> None:
    op.execute("drop table if exists epidemic_kg.kg_identity_quarantine")
    op.execute("drop table if exists epidemic_kg.kg_document_evidence_status")
