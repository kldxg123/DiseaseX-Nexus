"""add human feedback experience bank

Revision ID: 0007_add_human_feedback_experience_bank
Revises: 0006_add_predictions
Create Date: 2026-07-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0007_add_human_feedback_experience_bank"
down_revision = "0006_add_predictions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prediction_feedback",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("prediction_id", sa.Integer(), nullable=False),
        sa.Column("feedback_status", sa.String(length=40), nullable=False),
        sa.Column("actual_value", sa.Float(), nullable=True),
        sa.Column("actual_outbreak_label", sa.Boolean(), nullable=True),
        sa.Column("failure_reason", sa.String(length=80), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("reviewer_ref", sa.String(length=160), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["prediction_id"], ["predictions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_prediction_feedback_prediction",
        "prediction_feedback",
        ["prediction_id"],
        unique=True,
    )
    op.create_index(
        "ix_prediction_feedback_status",
        "prediction_feedback",
        ["feedback_status"],
    )
    op.create_index(
        "ix_prediction_feedback_failure_reason",
        "prediction_feedback",
        ["failure_reason"],
    )
    op.create_table(
        "model_experience_bank",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("prediction_id", sa.Integer(), nullable=False),
        sa.Column("feedback_id", sa.Integer(), nullable=False),
        sa.Column("country", sa.String(length=120), nullable=False),
        sa.Column("admin1", sa.String(length=120), nullable=True),
        sa.Column("disease", sa.String(length=120), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("iso_week", sa.String(length=10), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("model_name", sa.String(length=120), nullable=False),
        sa.Column("model_version", sa.String(length=120), nullable=False),
        sa.Column("predicted_value", sa.Float(), nullable=False),
        sa.Column("actual_value", sa.Float(), nullable=True),
        sa.Column("absolute_error", sa.Float(), nullable=True),
        sa.Column("signed_error", sa.Float(), nullable=True),
        sa.Column("predicted_outbreak_label", sa.Boolean(), nullable=True),
        sa.Column("actual_outbreak_label", sa.Boolean(), nullable=True),
        sa.Column("classification_correct", sa.Boolean(), nullable=True),
        sa.Column("feedback_status", sa.String(length=40), nullable=False),
        sa.Column("failure_reason", sa.String(length=80), nullable=True),
        sa.Column("experience_json", sa.JSON(), nullable=False),
        sa.Column("recommended_action", sa.String(length=160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["feedback_id"], ["prediction_feedback.id"]),
        sa.ForeignKeyConstraint(["prediction_id"], ["predictions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_model_experience_prediction",
        "model_experience_bank",
        ["prediction_id"],
        unique=True,
    )
    op.create_index(
        "ix_model_experience_feedback",
        "model_experience_bank",
        ["feedback_id"],
        unique=True,
    )
    op.create_index(
        "ix_model_experience_model",
        "model_experience_bank",
        ["model_name", "model_version"],
    )
    op.create_index(
        "ix_model_experience_target",
        "model_experience_bank",
        ["disease", "country", "target_date"],
    )
    op.create_index(
        "ix_model_experience_failure",
        "model_experience_bank",
        ["failure_reason"],
    )


def downgrade() -> None:
    op.drop_index("ix_model_experience_failure", table_name="model_experience_bank")
    op.drop_index("ix_model_experience_target", table_name="model_experience_bank")
    op.drop_index("ix_model_experience_model", table_name="model_experience_bank")
    op.drop_index("ix_model_experience_feedback", table_name="model_experience_bank")
    op.drop_index("ix_model_experience_prediction", table_name="model_experience_bank")
    op.drop_table("model_experience_bank")
    op.drop_index("ix_prediction_feedback_failure_reason", table_name="prediction_feedback")
    op.drop_index("ix_prediction_feedback_status", table_name="prediction_feedback")
    op.drop_index("ix_prediction_feedback_prediction", table_name="prediction_feedback")
    op.drop_table("prediction_feedback")
