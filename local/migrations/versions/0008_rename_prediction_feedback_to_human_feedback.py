"""rename prediction feedback table to human feedback

Revision ID: 0008_rename_prediction_feedback_to_human_feedback
Revises: 0007_add_human_feedback_experience_bank
Create Date: 2026-07-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0008_rename_prediction_feedback_to_human_feedback"
down_revision = "0007_add_human_feedback_experience_bank"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    return table_name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    has_old = _has_table("prediction_feedback")
    has_new = _has_table("human_feedback")
    if has_new and not has_old:
        return
    if has_old and has_new:
        raise RuntimeError("Both prediction_feedback and human_feedback exist; refusing unsafe rename.")
    if has_old:
        op.rename_table("prediction_feedback", "human_feedback")


def downgrade() -> None:
    has_old = _has_table("prediction_feedback")
    has_new = _has_table("human_feedback")
    if has_old and not has_new:
        return
    if has_old and has_new:
        raise RuntimeError("Both prediction_feedback and human_feedback exist; refusing unsafe downgrade rename.")
    if has_new:
        op.rename_table("human_feedback", "prediction_feedback")
