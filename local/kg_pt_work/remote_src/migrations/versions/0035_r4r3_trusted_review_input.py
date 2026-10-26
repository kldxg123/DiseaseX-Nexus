"""R4R3 trusted review input evidence contract.

Revision ID: 0035_r4r3_trusted_review_input
Revises: 0034_r4r2_core_verification_closure
Create Date: 2026-08-05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0035_r4r3_trusted_review_input"
down_revision = "0034_r4r2_core_verification_closure"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for col in [
        sa.Column("r4r3_classification", sa.Text(), nullable=True),
        sa.Column("r4r3_relation_type", sa.Text(), nullable=True),
        sa.Column("r4r3_model_registry_id", sa.Text(), nullable=True),
        sa.Column("r4r3_natural_key", sa.Text(), nullable=True),
        sa.Column("r4r3_char_start", sa.Integer(), nullable=True),
        sa.Column("r4r3_char_end", sa.Integer(), nullable=True),
        sa.Column("r4r3_canonical_evidence_sha256", sa.Text(), nullable=True),
        sa.Column("r4r3_missing_requirements", sa.JSON(), nullable=True),
        sa.Column("r4r3_classification_reason_code", sa.Text(), nullable=True),
        sa.Column("r4r3_checked_at", sa.DateTime(timezone=True), nullable=True),
    ]:
        op.add_column("kg_pending_claim_candidates", col, schema="epidemic_kg")

    op.create_table(
        "kg_r4r3_candidate_review_inputs",
        sa.Column("candidate_id", sa.UUID(), primary_key=True),
        sa.Column("natural_key", sa.Text(), nullable=False),
        sa.Column("work_id", sa.UUID(), nullable=True),
        sa.Column("work_identity_state", sa.Text(), nullable=False),
        sa.Column("document_version_id", sa.UUID(), nullable=False),
        sa.Column("pdf_sha256", sa.Text(), nullable=True),
        sa.Column("page_id", sa.UUID(), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("chunk_id", sa.UUID(), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=True),
        sa.Column("char_start", sa.Integer(), nullable=True),
        sa.Column("char_end", sa.Integer(), nullable=True),
        sa.Column("locator_version", sa.Text(), nullable=False),
        sa.Column("source_section_type", sa.Text(), nullable=False),
        sa.Column("source_section_heading", sa.Text(), nullable=True),
        sa.Column("evidence_text_raw", sa.Text(), nullable=False),
        sa.Column("evidence_text_canonical", sa.Text(), nullable=False),
        sa.Column("canonicalization_version", sa.Text(), nullable=False),
        sa.Column("evidence_text_sha256", sa.Text(), nullable=False),
        sa.Column("disease_id", sa.Text(), nullable=True),
        sa.Column("disease_text", sa.Text(), nullable=True),
        sa.Column("model_registry_id", sa.Text(), nullable=True),
        sa.Column("model_text", sa.Text(), nullable=True),
        sa.Column("relation_type", sa.Text(), nullable=False),
        sa.Column("relation_subject", sa.Text(), nullable=True),
        sa.Column("relation_object", sa.Text(), nullable=True),
        sa.Column("parameter_name", sa.Text(), nullable=True),
        sa.Column("parameter_value_raw", sa.Text(), nullable=True),
        sa.Column("parameter_value_normalized", sa.Text(), nullable=True),
        sa.Column("parameter_unit", sa.Text(), nullable=True),
        sa.Column("parameter_range_low", sa.Text(), nullable=True),
        sa.Column("parameter_range_high", sa.Text(), nullable=True),
        sa.Column("scope_geography", sa.Text(), nullable=True),
        sa.Column("scope_population_or_host", sa.Text(), nullable=True),
        sa.Column("scope_target_metric", sa.Text(), nullable=True),
        sa.Column("scope_time_grain", sa.Text(), nullable=True),
        sa.Column("scope_horizon", sa.Text(), nullable=True),
        sa.Column("scope_training_window", sa.Text(), nullable=True),
        sa.Column("extractor_name", sa.Text(), nullable=False),
        sa.Column("extractor_version", sa.Text(), nullable=False),
        sa.Column("classification", sa.Text(), nullable=False),
        sa.Column("classification_reason_code", sa.Text(), nullable=False),
        sa.Column("missing_requirements", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("review_status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="epidemic_kg",
    )
    op.create_index("ix_kg_r4r3_review_inputs_classification", "kg_r4r3_candidate_review_inputs", ["classification"], schema="epidemic_kg")
    op.create_index("ix_kg_r4r3_review_inputs_work_state", "kg_r4r3_candidate_review_inputs", ["work_identity_state"], schema="epidemic_kg")
    op.create_index("ix_kg_pending_claim_candidates_r4r3_classification", "kg_pending_claim_candidates", ["r4r3_classification"], schema="epidemic_kg")


def downgrade() -> None:
    op.drop_index("ix_kg_pending_claim_candidates_r4r3_classification", table_name="kg_pending_claim_candidates", schema="epidemic_kg")
    op.drop_index("ix_kg_r4r3_review_inputs_work_state", table_name="kg_r4r3_candidate_review_inputs", schema="epidemic_kg")
    op.drop_index("ix_kg_r4r3_review_inputs_classification", table_name="kg_r4r3_candidate_review_inputs", schema="epidemic_kg")
    op.drop_table("kg_r4r3_candidate_review_inputs", schema="epidemic_kg")
    for col in [
        "r4r3_checked_at",
        "r4r3_classification_reason_code",
        "r4r3_missing_requirements",
        "r4r3_canonical_evidence_sha256",
        "r4r3_char_end",
        "r4r3_char_start",
        "r4r3_natural_key",
        "r4r3_model_registry_id",
        "r4r3_relation_type",
        "r4r3_classification",
    ]:
        op.drop_column("kg_pending_claim_candidates", col, schema="epidemic_kg")
