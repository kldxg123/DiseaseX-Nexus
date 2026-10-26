"""add predictions

Revision ID: 0006_add_predictions
Revises: 0005_add_model_features
Create Date: 2026-07-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0006_add_predictions"
down_revision = "0005_add_model_features"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "predictions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("country", sa.String(length=120), nullable=False),
        sa.Column("admin1", sa.String(length=120), nullable=True),
        sa.Column("disease", sa.String(length=120), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("iso_week", sa.String(length=10), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("model_name", sa.String(length=120), nullable=False),
        sa.Column("model_version", sa.String(length=120), nullable=False),
        sa.Column("predicted_value", sa.Float(), nullable=False),
        sa.Column("lower_bound", sa.Float(), nullable=False),
        sa.Column("upper_bound", sa.Float(), nullable=False),
        sa.Column("uncertainty_method", sa.String(length=120), nullable=False),
        sa.Column("prediction_interval_json", sa.JSON(), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("provenance_json", sa.JSON(), nullable=False),
        sa.Column("review_status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_predictions_business_key",
        "predictions",
        [
            "country",
            "admin1",
            "disease",
            "iso_week",
            "horizon_days",
            "model_name",
            "model_version",
        ],
        unique=True,
    )
    op.create_index(
        "ix_predictions_target",
        "predictions",
        ["disease", "country", "target_date"],
    )
    op.create_index("ix_predictions_created_at", "predictions", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_predictions_created_at", table_name="predictions")
    op.drop_index("ix_predictions_target", table_name="predictions")
    op.drop_index("ix_predictions_business_key", table_name="predictions")
    op.drop_table("predictions")
