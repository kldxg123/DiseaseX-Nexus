"""R4R3CR2 semantic evidence closure fields.

Revision ID: 0037_r4r3cr2_semantic_evidence_closure
Revises: 0036_r4r3cr_closure_repair
Create Date: 2026-08-06
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0037_r4r3cr2_semantic_evidence_closure"
down_revision = "0036_r4r3cr_closure_repair"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for col in [
        sa.Column("r4r3cr2_run_id", sa.Text(), nullable=True),
        sa.Column("r4r3cr2_classification", sa.Text(), nullable=True),
        sa.Column("r4r3cr2_machine_review_lane", sa.Text(), nullable=True),
        sa.Column("r4r3cr2_human_review_status", sa.Text(), nullable=True),
        sa.Column("r4r3cr2_disease_id", sa.Text(), nullable=True),
        sa.Column("r4r3cr2_relation_type", sa.Text(), nullable=True),
        sa.Column("r4r3cr2_relation_polarity", sa.Text(), nullable=True),
        sa.Column("r4r3cr2_model_registry_id", sa.Text(), nullable=True),
        sa.Column("r4r3cr2_missing_requirements", sa.JSON(), nullable=True),
        sa.Column("r4r3cr2_classification_reason_code", sa.Text(), nullable=True),
        sa.Column("r4r3cr2_scope_evidence_refs", sa.JSON(), nullable=True),
        sa.Column("r4r3cr2_source_section_type", sa.Text(), nullable=True),
        sa.Column("r4r3cr2_source_section_heading", sa.Text(), nullable=True),
        sa.Column("r4r3cr2_checked_at", sa.DateTime(timezone=True), nullable=True),
    ]:
        op.add_column("kg_pending_claim_candidates", col, schema="epidemic_kg")

    op.create_table(
        "kg_r4r3cr2_candidate_review_inputs",
        sa.Column("candidate_id", sa.UUID(), primary_key=True),
        sa.Column("current_run_id", sa.Text(), nullable=False),
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
        sa.Column("source_section_type", sa.Text(), nullable=False),
        sa.Column("source_section_heading", sa.Text(), nullable=False),
        sa.Column("section_detection_method", sa.Text(), nullable=False),
        sa.Column("section_detection_confidence", sa.Float(), nullable=False),
        sa.Column("evidence_text_raw", sa.Text(), nullable=False),
        sa.Column("evidence_text_canonical", sa.Text(), nullable=False),
        sa.Column("evidence_text_sha256", sa.Text(), nullable=False),
        sa.Column("disease_id", sa.Text(), nullable=True),
        sa.Column("disease_text", sa.Text(), nullable=True),
        sa.Column("model_registry_id", sa.Text(), nullable=True),
        sa.Column("model_text", sa.Text(), nullable=True),
        sa.Column("relation_type", sa.Text(), nullable=False),
        sa.Column("relation_subject", sa.Text(), nullable=True),
        sa.Column("relation_object", sa.Text(), nullable=True),
        sa.Column("relation_polarity", sa.Text(), nullable=False),
        sa.Column("cited_work_id_or_null", sa.Text(), nullable=True),
        sa.Column("scope_geography", sa.Text(), nullable=True),
        sa.Column("scope_population_or_host", sa.Text(), nullable=True),
        sa.Column("scope_target_metric", sa.Text(), nullable=True),
        sa.Column("scope_time_grain", sa.Text(), nullable=True),
        sa.Column("scope_horizon", sa.Text(), nullable=True),
        sa.Column("scope_training_window", sa.Text(), nullable=True),
        sa.Column("scope_evidence_refs", sa.JSON(), nullable=False),
        sa.Column("classification", sa.Text(), nullable=False),
        sa.Column("classification_reason_code", sa.Text(), nullable=False),
        sa.Column("missing_requirements", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("machine_review_lane", sa.Text(), nullable=False),
        sa.Column("human_review_status", sa.Text(), nullable=False, server_default="NOT_HUMAN_REVIEWED"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="epidemic_kg",
    )
    op.create_index(
        "ix_kg_r4r3cr2_review_inputs_run",
        "kg_r4r3cr2_candidate_review_inputs",
        ["current_run_id"],
        schema="epidemic_kg",
    )
    op.create_index(
        "ix_kg_r4r3cr2_review_inputs_lane",
        "kg_r4r3cr2_candidate_review_inputs",
        ["machine_review_lane"],
        schema="epidemic_kg",
    )
    op.create_index(
        "ix_kg_pending_claim_candidates_r4r3cr2_classification",
        "kg_pending_claim_candidates",
        ["r4r3cr2_classification"],
        schema="epidemic_kg",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_kg_pending_claim_candidates_r4r3cr2_classification",
        table_name="kg_pending_claim_candidates",
        schema="epidemic_kg",
    )
    op.drop_index("ix_kg_r4r3cr2_review_inputs_lane", table_name="kg_r4r3cr2_candidate_review_inputs", schema="epidemic_kg")
    op.drop_index("ix_kg_r4r3cr2_review_inputs_run", table_name="kg_r4r3cr2_candidate_review_inputs", schema="epidemic_kg")
    op.drop_table("kg_r4r3cr2_candidate_review_inputs", schema="epidemic_kg")
    for col in [
        "r4r3cr2_checked_at",
        "r4r3cr2_source_section_heading",
        "r4r3cr2_source_section_type",
        "r4r3cr2_scope_evidence_refs",
        "r4r3cr2_classification_reason_code",
        "r4r3cr2_missing_requirements",
        "r4r3cr2_model_registry_id",
        "r4r3cr2_relation_polarity",
        "r4r3cr2_relation_type",
        "r4r3cr2_disease_id",
        "r4r3cr2_human_review_status",
        "r4r3cr2_machine_review_lane",
        "r4r3cr2_classification",
        "r4r3cr2_run_id",
    ]:
        op.drop_column("kg_pending_claim_candidates", col, schema="epidemic_kg")
