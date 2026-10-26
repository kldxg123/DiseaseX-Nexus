"""add epidemic events

Revision ID: 0004_add_epidemic_events
Revises: 0003_add_raw_documents
Create Date: 2026-07-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0004_add_epidemic_events"
down_revision = "0003_add_raw_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "raw_documents",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("raw_documents", sa.Column("country_detected", sa.String(length=120), nullable=True))
    op.add_column("raw_documents", sa.Column("disease_detected", sa.String(length=120), nullable=True))
    op.add_column(
        "raw_documents",
        sa.Column(
            "extraction_status",
            sa.String(length=40),
            nullable=False,
            server_default="pending",
        ),
    )
    op.create_index(
        "ix_raw_documents_extraction_status",
        "raw_documents",
        ["extraction_status"],
    )
    op.create_table(
        "epidemic_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("raw_document_id", sa.Integer(), nullable=False),
        sa.Column("country", sa.String(length=120), nullable=True),
        sa.Column("admin1", sa.String(length=120), nullable=True),
        sa.Column("admin2", sa.String(length=120), nullable=True),
        sa.Column("disease", sa.String(length=120), nullable=True),
        sa.Column("event_date", sa.Date(), nullable=True),
        sa.Column("iso_week", sa.String(length=10), nullable=True),
        sa.Column("cases", sa.Integer(), nullable=True),
        sa.Column("suspected_cases", sa.Integer(), nullable=True),
        sa.Column("confirmed_cases", sa.Integer(), nullable=True),
        sa.Column("deaths", sa.Integer(), nullable=True),
        sa.Column("hospitalizations", sa.Integer(), nullable=True),
        sa.Column("tests", sa.Integer(), nullable=True),
        sa.Column("positivity_rate", sa.Float(), nullable=True),
        sa.Column("outbreaks_count", sa.Integer(), nullable=True),
        sa.Column("setting", sa.String(length=120), nullable=True),
        sa.Column("pathogen_genotype", sa.String(length=120), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column("extraction_method", sa.String(length=80), nullable=False),
        sa.Column("provenance_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["raw_document_id"], ["raw_documents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_epidemic_events_raw_document",
        "epidemic_events",
        ["raw_document_id"],
        unique=True,
    )
    op.create_index(
        "ix_epidemic_events_disease_country_date",
        "epidemic_events",
        ["disease", "country", "event_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_epidemic_events_disease_country_date", table_name="epidemic_events")
    op.drop_index("ix_epidemic_events_raw_document", table_name="epidemic_events")
    op.drop_table("epidemic_events")
    op.drop_index("ix_raw_documents_extraction_status", table_name="raw_documents")
    op.drop_column("raw_documents", "extraction_status")
    op.drop_column("raw_documents", "disease_detected")
    op.drop_column("raw_documents", "country_detected")
    op.drop_column("raw_documents", "published_at")
