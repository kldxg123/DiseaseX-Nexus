"""add raw documents

Revision ID: 0003_add_raw_documents
Revises: 0002_add_source_registry
Create Date: 2026-06-30
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003_add_raw_documents"
down_revision = "0002_add_source_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "raw_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_registry_id", sa.Integer(), nullable=False),
        sa.Column("original_url", sa.Text(), nullable=False),
        sa.Column("normalized_url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("content_type", sa.String(length=200), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_registry_id"], ["source_registry.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_raw_documents_content_hash",
        "raw_documents",
        ["content_hash"],
        unique=True,
    )
    op.create_index("ix_raw_documents_fetched_at", "raw_documents", ["fetched_at"])
    op.create_index(
        "ix_raw_documents_source_url",
        "raw_documents",
        ["source_registry_id", "normalized_url"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_raw_documents_source_url", table_name="raw_documents")
    op.drop_index("ix_raw_documents_fetched_at", table_name="raw_documents")
    op.drop_index("ix_raw_documents_content_hash", table_name="raw_documents")
    op.drop_table("raw_documents")
