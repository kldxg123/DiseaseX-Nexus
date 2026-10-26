"""R4R2 core verification closure evidence fields.

Revision ID: 0034_r4r2_core_verification_closure
Revises: 0033_r4r1_identity_evidence_tests_forecast
Create Date: 2026-08-05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0034_r4r2_core_verification_closure"
down_revision = "0033_r4r1_identity_evidence_tests_forecast"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("kg_pending_claim_candidates", sa.Column("r4r2_disposition", sa.Text(), nullable=True), schema="epidemic_kg")
    op.add_column("kg_pending_claim_candidates", sa.Column("r4r2_semantic_status", sa.Text(), nullable=True), schema="epidemic_kg")
    op.add_column("kg_pending_claim_candidates", sa.Column("r4r2_missing_fields", sa.JSON(), nullable=True), schema="epidemic_kg")
    op.add_column("kg_pending_claim_candidates", sa.Column("r4r2_relation_evidence", sa.Text(), nullable=True), schema="epidemic_kg")
    op.add_column("kg_pending_claim_candidates", sa.Column("r4r2_review_ready", sa.Boolean(), nullable=True), schema="epidemic_kg")
    op.add_column("kg_pending_claim_candidates", sa.Column("r4r2_extractor_version", sa.Text(), nullable=True), schema="epidemic_kg")
    op.add_column("kg_pending_claim_candidates", sa.Column("r4r2_checked_at", sa.DateTime(timezone=True), nullable=True), schema="epidemic_kg")

    op.create_table(
        "kg_r4r2_candidate_evidence_audit",
        sa.Column("candidate_id", sa.UUID(), nullable=False),
        sa.Column("document_node_id", sa.UUID(), nullable=False),
        sa.Column("work_node_id", sa.UUID(), nullable=True),
        sa.Column("source_pdf_sha256", sa.Text(), nullable=True),
        sa.Column("evidence_text_sha256", sa.Text(), nullable=True),
        sa.Column("page_id", sa.UUID(), nullable=True),
        sa.Column("chunk_id", sa.UUID(), nullable=True),
        sa.Column("physical_page", sa.Integer(), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=True),
        sa.Column("disposition", sa.Text(), nullable=False),
        sa.Column("semantic_status", sa.Text(), nullable=False),
        sa.Column("review_ready", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reverse_locator_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("relation_evidence", sa.Text(), nullable=True),
        sa.Column("missing_fields", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("model_text", sa.Text(), nullable=True),
        sa.Column("disease_text", sa.Text(), nullable=True),
        sa.Column("parameter_name", sa.Text(), nullable=True),
        sa.Column("parameter_value", sa.Text(), nullable=True),
        sa.Column("audit_version", sa.Text(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("candidate_id"),
        schema="epidemic_kg",
    )
    op.create_index("ix_kg_r4r2_candidate_audit_disposition", "kg_r4r2_candidate_evidence_audit", ["disposition"], schema="epidemic_kg")
    op.create_index("ix_kg_pending_claim_candidates_r4r2_disposition", "kg_pending_claim_candidates", ["r4r2_disposition"], schema="epidemic_kg")


def downgrade() -> None:
    op.drop_index("ix_kg_pending_claim_candidates_r4r2_disposition", table_name="kg_pending_claim_candidates", schema="epidemic_kg")
    op.drop_index("ix_kg_r4r2_candidate_audit_disposition", table_name="kg_r4r2_candidate_evidence_audit", schema="epidemic_kg")
    op.drop_table("kg_r4r2_candidate_evidence_audit", schema="epidemic_kg")
    for col in [
        "r4r2_checked_at",
        "r4r2_extractor_version",
        "r4r2_review_ready",
        "r4r2_relation_evidence",
        "r4r2_missing_fields",
        "r4r2_semantic_status",
        "r4r2_disposition",
    ]:
        op.drop_column("kg_pending_claim_candidates", col, schema="epidemic_kg")
