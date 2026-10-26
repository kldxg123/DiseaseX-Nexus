"""add source registry

Revision ID: 0002_add_source_registry
Revises: 0001_initial_schema
Create Date: 2026-06-30
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002_add_source_registry"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_registry",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("country_scope", sa.JSON(), nullable=False),
        sa.Column("disease_scope", sa.JSON(), nullable=False),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("authority_score", sa.Float(), nullable=False),
        sa.Column("relevance_score", sa.Float(), nullable=False),
        sa.Column("freshness_score", sa.Float(), nullable=False),
        sa.Column("reliability_score", sa.Float(), nullable=False),
        sa.Column("structured_data_score", sa.Float(), nullable=False),
        sa.Column("historical_success_score", sa.Float(), nullable=False),
        sa.Column("language_country_match", sa.Float(), nullable=False),
        sa.Column("legal_or_robots_penalty", sa.Float(), nullable=False),
        sa.Column("robots_allowed", sa.Boolean(), nullable=True),
        sa.Column("parser_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_source_registry_base_url", "source_registry", ["base_url"], unique=True)
    op.create_index(
        "ix_source_registry_country_disease",
        "source_registry",
        ["country_scope", "disease_scope"],
    )
    op.create_index("ix_source_registry_status", "source_registry", ["status"])


def downgrade() -> None:
    op.drop_index("ix_source_registry_status", table_name="source_registry")
    op.drop_index("ix_source_registry_country_disease", table_name="source_registry")
    op.drop_index("ix_source_registry_base_url", table_name="source_registry")
    op.drop_table("source_registry")
