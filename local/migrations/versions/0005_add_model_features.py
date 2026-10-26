"""add model features

Revision ID: 0005_add_model_features
Revises: 0004_add_epidemic_events
Create Date: 2026-07-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0005_add_model_features"
down_revision = "0004_add_epidemic_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_features",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("country", sa.String(length=120), nullable=False),
        sa.Column("admin1", sa.String(length=120), nullable=True),
        sa.Column("disease", sa.String(length=120), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("iso_week", sa.String(length=10), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("feature_json", sa.JSON(), nullable=False),
        sa.Column("target_value", sa.Float(), nullable=False),
        sa.Column("target_outbreak_label", sa.Boolean(), nullable=False),
        sa.Column("split_type", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_model_features_business_key",
        "model_features",
        ["country", "admin1", "disease", "iso_week", "horizon_days"],
        unique=True,
    )
    op.create_index(
        "ix_model_features_target",
        "model_features",
        ["disease", "country", "target_date"],
    )
    op.create_index("ix_model_features_split", "model_features", ["split_type"])


def downgrade() -> None:
    op.drop_index("ix_model_features_split", table_name="model_features")
    op.drop_index("ix_model_features_target", table_name="model_features")
    op.drop_index("ix_model_features_business_key", table_name="model_features")
    op.drop_table("model_features")
