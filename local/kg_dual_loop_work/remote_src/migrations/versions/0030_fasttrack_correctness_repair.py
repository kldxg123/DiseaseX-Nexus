"""Fasttrack correctness repair forecast metadata.

Revision ID: 0030_fasttrack_correctness_repair
Revises: 0029_p1_17a_prediction_mvp
Create Date: 2026-07-26
"""
from __future__ import annotations

from alembic import op

revision = "0030_fasttrack_correctness_repair"
down_revision = "0029_p1_17a_prediction_mvp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Ownership-safe marker migration. The A100 database has forecast tables
    # owned by the admin repair principal, while Alembic runs with a narrower
    # migration principal. The concrete idempotent DDL is applied by the
    # controlled fasttrack repair schema gate before this migration is stamped.
    op.execute("SELECT 1")


def downgrade() -> None:
    op.execute("SELECT 1")
